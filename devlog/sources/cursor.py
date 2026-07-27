"""
Parses Cursor agent transcripts (~/.cursor/projects/*/agent-transcripts/**/*.jsonl)
into RawSession + SessionEvent records. Day slicing is handled by digest.slice_for_date.

Does not read VS Code SQLite chat databases.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path

from devlog.models import RawSession, SessionEvent
from devlog.sources.base import register

_USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL | re.IGNORECASE)
_TIMESTAMP_RE = re.compile(r"<timestamp>\s*(.*?)\s*</timestamp>", re.DOTALL | re.IGNORECASE)


def decode_cursor_project_folder(folder_name: str) -> str:
    """Decode Cursor project folder names like c-Users-dev-code-devlog.

    Cursor encodes absolute Windows paths by lowercasing the drive letter and
    replacing path separators with '-'.
    """
    name = folder_name.strip()
    if not name:
        return name
    parts = name.split("-")
    if len(parts) >= 2 and len(parts[0]) == 1 and parts[0].isalpha():
        drive = parts[0].upper()
        rest = "/".join(parts[1:])
        return f"{drive}:/{rest}"
    # Unix-style or unknown: join with slashes
    if name.startswith("-"):
        return "/" + name[1:].replace("-", "/")
    return "/" + name.replace("-", "/")


_UTC_OFFSET_RE = re.compile(r"UTC\s*([+-])\s*(\d{1,2})(?::(\d{2}))?", re.IGNORECASE)


def _tz_from_hint(raw: str) -> tzinfo:
    """Resolve a timezone from Cursor's '(UTC+5:30)'-style hint; default UTC."""
    m = _UTC_OFFSET_RE.search(raw)
    if not m:
        return UTC
    sign = 1 if m.group(1) == "+" else -1
    hours = int(m.group(2))
    minutes = int(m.group(3) or 0)
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _parse_embedded_timestamp(text: str) -> datetime | None:
    m = _TIMESTAMP_RE.search(text)
    if not m:
        return None
    raw = m.group(1).strip()
    # Example: Monday, Jul 20, 2026, 3:30 PM (UTC+5:30)
    if "," in raw:
        raw_no_weekday = raw.split(",", 1)[1].strip()
    else:
        raw_no_weekday = raw
    if "(" in raw_no_weekday:
        raw_no_weekday = raw_no_weekday.partition("(")[0].strip()

    for fmt in ("%b %d, %Y, %I:%M %p", "%B %d, %Y, %I:%M %p"):
        try:
            dt = datetime.strptime(raw_no_weekday, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=_tz_from_hint(raw))
    return None


def _extract_user_text(text: str) -> str | None:
    m = _USER_QUERY_RE.search(text)
    if m:
        q = m.group(1).strip()
        return q[:400] + ("…" if len(q) > 400 else "") if q else None
    stripped = text.strip()
    if not stripped:
        return None
    # Skip Cursor slash-command chrome without a real query.
    if "<command-name>" in stripped or "<command-message>" in stripped:
        return None
    if len(stripped) > 400:
        stripped = stripped[:399].rstrip() + "…"
    return stripped


def _content_texts(content: object) -> list[str]:
    out: list[str] = []
    if isinstance(content, str) and content.strip():
        out.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str) and t.strip():
                    out.append(t)
    return out


def _tool_path_and_cmd(tool_input: object) -> tuple[str | None, str | None]:
    if not isinstance(tool_input, dict):
        return None, None
    fp = (
        tool_input.get("path")
        or tool_input.get("file_path")
        or tool_input.get("target_directory")
        or tool_input.get("working_directory")
    )
    cmd = tool_input.get("command")
    return (fp if isinstance(fp, str) else None), (cmd if isinstance(cmd, str) else None)


def parse_transcript_file(path: Path, project_folder: str) -> RawSession | None:
    session_id = path.stem
    project_path = decode_cursor_project_folder(project_folder)
    timestamps: list[datetime] = []
    events: list[SessionEvent] = []
    mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue

            role = obj.get("role")
            msg = obj.get("message")
            if not isinstance(msg, dict):
                msg = {}
            content = msg.get("content")

            if role == "user":
                for text in _content_texts(content):
                    ts = _parse_embedded_timestamp(text) or mtime
                    timestamps.append(ts)
                    user_text = _extract_user_text(text)
                    if user_text:
                        events.append(SessionEvent(timestamp=ts, user_message=user_text))
            elif role == "assistant":
                # Prefer last known timestamp; else mtime
                ts = timestamps[-1] if timestamps else mtime
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        name = block.get("name") or "unknown_tool"
                        fp, cmd = _tool_path_and_cmd(block.get("input"))
                        events.append(
                            SessionEvent(
                                timestamp=ts,
                                tool_name=str(name),
                                file_path=fp,
                                bash_command=cmd,
                            )
                        )
                # Still count assistant turns toward session presence via timestamp
                if not timestamps:
                    timestamps.append(mtime)

    if not timestamps and not events:
        return None
    if not timestamps:
        timestamps = [mtime]
    if not events:
        return None

    return RawSession(
        session_id=session_id,
        project_path=project_path,
        source="cursor",
        start_time=min(timestamps),
        end_time=max(timestamps),
        events=events,
    )


class CursorParser:
    name = "cursor"

    def iter_sessions(self, root: Path) -> list[RawSession]:
        root = Path(root)
        projects_dir = root / "projects" if (root / "projects").exists() else root
        if not projects_dir.exists():
            return []

        sessions: list[RawSession] = []
        for project_folder in sorted(projects_dir.iterdir()):
            if not project_folder.is_dir():
                continue
            transcripts = project_folder / "agent-transcripts"
            if not transcripts.is_dir():
                continue
            for path in sorted(transcripts.rglob("*.jsonl")):
                session = parse_transcript_file(path, project_folder.name)
                if session is not None:
                    sessions.append(session)
        return sessions


register(CursorParser())
