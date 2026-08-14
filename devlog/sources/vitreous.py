"""
Parses Vitreous session JSONL if/when the desktop persists transcripts.

Looks under <root>/sessions and <root>/.vitreous/sessions. The nvidia-skills
cache is not a session store and is ignored. Persistence is not shipped yet,
so a missing tree yields no sessions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from devlog.models import RawSession, SessionEvent
from devlog.sources.base import register


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


def parse_session_file(path: Path, *, source: str = "vitreous") -> RawSession | None:
    timestamps: list[datetime] = []
    events: list[SessionEvent] = []
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        mtime = None

    try:
        fh = path.open(encoding="utf-8")
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
            ts = _parse_timestamp(obj.get("timestamp") or obj.get("ts")) or mtime
            if ts is None:
                continue
            timestamps.append(ts)
            etype = obj.get("type") or obj.get("role")
            if etype == "user":
                content = obj.get("content") or obj.get("text") or obj.get("user_message")
                if isinstance(content, str) and content.strip():
                    events.append(SessionEvent(timestamp=ts, user_message=_truncate(content)))
            elif etype in {"assistant", "tool"}:
                name = obj.get("tool_name") or obj.get("name")
                fp = obj.get("path") or obj.get("file_path")
                cmd = obj.get("command") or obj.get("bash_command")
                if isinstance(name, str) and name:
                    events.append(
                        SessionEvent(
                            timestamp=ts,
                            tool_name=name,
                            file_path=fp if isinstance(fp, str) else None,
                            bash_command=cmd if isinstance(cmd, str) else None,
                        )
                    )

    if not events or not timestamps:
        return None
    return RawSession(
        session_id=path.stem,
        project_path=path.parent.as_posix(),
        source=source,
        start_time=min(timestamps),
        end_time=max(timestamps),
        events=events,
    )


class VitreousParser:
    name = "vitreous"

    def iter_sessions(self, root: Path) -> list[RawSession]:
        root = Path(root)
        if not root.exists():
            return []
        session_dirs = [root / "sessions", root / ".vitreous" / "sessions"]
        sessions: list[RawSession] = []
        for sdir in session_dirs:
            if not sdir.is_dir():
                continue
            if "nvidia-skills" in sdir.parts:
                continue
            for path in sorted(sdir.rglob("*.jsonl")):
                if "nvidia-skills" in path.parts:
                    continue
                try:
                    session = parse_session_file(path)
                except (OSError, UnicodeError):
                    continue
                if session is not None:
                    sessions.append(session)
        return sessions


register(VitreousParser())
