"""
Parses Grok CLI local sessions (~/.grok/sessions/<url-encoded-cwd>/<uuid>/)
into RawSession + SessionEvent records. Day slicing is handled by digest.slice_for_date.

Layout (as of 2026):
  chat_history.jsonl  — user / assistant / tool_result / system turns
  events.jsonl        — tool_started / tool_completed with timestamps
  summary.json        — cwd, git_root_dir, created_at
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from devlog.models import RawSession, SessionEvent
from devlog.sources.base import register

_USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL | re.IGNORECASE)
_CHROME_MARKERS = (
    "<user_info>",
    "<git_status>",
    "<system-reminder>",
    "<system_reminder>",
    "<system_notification>",
)


def decode_grok_cwd_folder(folder_name: str) -> str:
    """URL-decode a Grok session parent folder (often an encoded absolute cwd)."""
    return unquote(folder_name).replace("\\", "/").rstrip("/")


def _parse_timestamp(ts: object) -> datetime | None:
    if not isinstance(ts, str) or not ts.strip():
        return None
    raw = ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _truncate(text: str, limit: int = 400) -> str:
    text = text.strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


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
            elif isinstance(block, str) and block.strip():
                out.append(block)
    return out


def _extract_user_text(text: str) -> str | None:
    m = _USER_QUERY_RE.search(text)
    if m:
        q = m.group(1).strip()
        return _truncate(q) if q else None
    stripped = text.strip()
    if not stripped:
        return None
    low = stripped.lower()
    if any(marker in low for marker in _CHROME_MARKERS):
        return None
    return _truncate(stripped)


def _tool_path_and_cmd(raw_input: object) -> tuple[str | None, str | None]:
    parsed: object = raw_input
    if isinstance(raw_input, str) and raw_input.strip():
        try:
            parsed = json.loads(raw_input)
        except json.JSONDecodeError:
            parsed = {"command": raw_input} if raw_input else {}
    if not isinstance(parsed, dict):
        return None, None
    fp = (
        parsed.get("target_file")
        or parsed.get("path")
        or parsed.get("file_path")
        or parsed.get("target_directory")
        or parsed.get("working_directory")
    )
    cmd = parsed.get("command") or parsed.get("cmd")
    return (fp if isinstance(fp, str) else None), (cmd if isinstance(cmd, str) else None)


def _load_summary(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _project_path(summary: dict, session_dir: Path) -> str:
    info = summary.get("info")
    if not isinstance(info, dict):
        info = {}
    cwd = info.get("cwd") or summary.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.replace("\\", "/").rstrip("/")
    git_root = summary.get("git_root_dir")
    if isinstance(git_root, str) and git_root.strip():
        return git_root.replace("\\", "/").rstrip("/")
    return decode_grok_cwd_folder(session_dir.parent.name)


def _load_event_times(path: Path) -> tuple[dict[str, datetime], list[datetime], list[datetime]]:
    """Return (tool_call_id -> ts, turn_started times, all timestamps)."""
    by_id: dict[str, datetime] = {}
    turns: list[datetime] = []
    all_ts: list[datetime] = []
    if not path.is_file():
        return by_id, turns, all_ts
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return by_id, turns, all_ts
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        ts = _parse_timestamp(obj.get("ts") or obj.get("timestamp"))
        if ts is None:
            continue
        all_ts.append(ts)
        etype = obj.get("type")
        if etype == "turn_started":
            turns.append(ts)
        cid = obj.get("tool_call_id")
        if isinstance(cid, str) and cid and etype in {"tool_completed", "tool_started"}:
            by_id.setdefault(cid, ts)
    return by_id, turns, all_ts


def parse_session_dir(session_dir: Path) -> RawSession | None:
    session_dir = Path(session_dir)
    chat = session_dir / "chat_history.jsonl"
    if not chat.is_file():
        return None

    summary = _load_summary(session_dir / "summary.json")
    info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
    session_id = session_dir.name
    sid = info.get("id") or summary.get("id")
    if isinstance(sid, str) and sid:
        session_id = sid

    project_path = _project_path(summary, session_dir)
    tool_ts, turn_times, event_times = _load_event_times(session_dir / "events.jsonl")
    created = _parse_timestamp(summary.get("created_at"))
    updated = _parse_timestamp(summary.get("updated_at"))
    try:
        mtime = datetime.fromtimestamp(chat.stat().st_mtime, tz=UTC)
    except OSError:
        mtime = None

    fallback = created or (min(event_times) if event_times else None) or mtime
    timestamps: list[datetime] = []
    events: list[SessionEvent] = []
    user_i = 0

    try:
        fh = chat.open(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue

            etype = obj.get("type")
            if etype == "system":
                continue
            if etype == "user":
                if obj.get("synthetic_reason"):
                    continue
                for text in _content_texts(obj.get("content")):
                    user_text = _extract_user_text(text)
                    if not user_text:
                        continue
                    if user_i < len(turn_times):
                        ts = turn_times[user_i]
                    else:
                        ts = fallback
                    user_i += 1
                    if ts is None:
                        continue
                    timestamps.append(ts)
                    events.append(SessionEvent(timestamp=ts, user_message=user_text))
            elif etype == "assistant":
                calls = obj.get("tool_calls")
                if not isinstance(calls, list):
                    continue
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    name = call.get("name") or "unknown_tool"
                    cid = call.get("id")
                    ts = tool_ts.get(cid) if isinstance(cid, str) else None
                    if ts is None:
                        ts = fallback
                    if ts is None:
                        continue
                    fp, cmd = _tool_path_and_cmd(call.get("arguments") or call.get("input"))
                    timestamps.append(ts)
                    events.append(
                        SessionEvent(
                            timestamp=ts,
                            tool_name=str(name),
                            file_path=fp,
                            bash_command=cmd,
                        )
                    )

    if not events:
        return None
    if not timestamps:
        if fallback is None:
            return None
        timestamps = [fallback]

    if created:
        timestamps.append(created)
    if updated:
        timestamps.append(updated)

    return RawSession(
        session_id=session_id,
        project_path=project_path,
        source="grok",
        start_time=min(timestamps),
        end_time=max(timestamps),
        events=events,
    )


class GrokParser:
    name = "grok"

    def iter_sessions(self, root: Path) -> list[RawSession]:
        root = Path(root)
        sessions_dir = root / "sessions" if (root / "sessions").exists() else root
        if not sessions_dir.exists():
            return []

        sessions: list[RawSession] = []
        for path in sorted(sessions_dir.rglob("chat_history.jsonl")):
            try:
                session = parse_session_dir(path.parent)
            except (OSError, UnicodeError):
                continue
            if session is not None:
                sessions.append(session)
        return sessions


register(GrokParser())
