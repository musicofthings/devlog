"""
Parses GitHub Copilot CLI session logs (~/.copilot/session-state/<uuid>/events.jsonl)
into RawSession + SessionEvent records. Day slicing is handled by digest.slice_for_date.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from devlog.models import RawSession, SessionEvent
from devlog.sources.base import register

_SKIP_USER_SOURCES = {"system"}
_CHROME_PREFIXES = (
    "<system_reminder>",
    "<system-reminder>",
    "<skill-context",
    "<current_datetime>",
)


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


def _is_chrome_user(text: str, source: object) -> bool:
    if isinstance(source, str):
        if (
            source in _SKIP_USER_SOURCES
            or source.startswith("skill-")
            or source.startswith("agent-")
        ):
            return True
    stripped = text.lstrip()
    return any(stripped.startswith(p) for p in _CHROME_PREFIXES)


def _tool_path_and_cmd(raw_input: object) -> tuple[str | None, str | None]:
    parsed: object = raw_input
    if isinstance(raw_input, str) and raw_input.strip():
        try:
            parsed = json.loads(raw_input)
        except json.JSONDecodeError:
            return None, raw_input.strip()[:200]
    if not isinstance(parsed, dict):
        return None, None
    fp = (
        parsed.get("path")
        or parsed.get("file_path")
        or parsed.get("target_file")
        or parsed.get("target_directory")
        or parsed.get("cwd")
    )
    cmd = parsed.get("command") or parsed.get("cmd")
    return (fp if isinstance(fp, str) else None), (cmd if isinstance(cmd, str) else None)


def _as_data(obj: dict) -> dict:
    data = obj.get("data")
    return data if isinstance(data, dict) else {}


def parse_events_file(path: Path) -> RawSession | None:
    session_id = path.parent.name
    project_path: str | None = None
    timestamps: list[datetime] = []
    events: list[SessionEvent] = []
    seen_tool_keys: set[tuple[str, str | None, str | None]] = set()

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

            ts = _parse_timestamp(obj.get("timestamp"))
            if ts is None:
                continue
            timestamps.append(ts)
            data = _as_data(obj)
            etype = obj.get("type")

            if etype == "session.start":
                sid = data.get("sessionId") or data.get("session_id")
                if isinstance(sid, str) and sid:
                    session_id = sid
                ctx = data.get("context")
                if isinstance(ctx, dict):
                    cwd = ctx.get("cwd") or ctx.get("gitRoot")
                    if isinstance(cwd, str) and cwd:
                        project_path = cwd.replace("\\", "/").rstrip("/")
                continue

            if etype == "user.message":
                content = data.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                if _is_chrome_user(content, data.get("source")):
                    continue
                events.append(SessionEvent(timestamp=ts, user_message=_truncate(content)))
                continue

            if etype in {"tool.execution_start", "assistant.message"}:
                requests: list[dict] = []
                if etype == "tool.execution_start":
                    requests.append(
                        {
                            "name": data.get("toolName") or data.get("tool_name") or "unknown_tool",
                            "arguments": data.get("arguments") or data.get("args"),
                        }
                    )
                else:
                    requests.extend(
                        tr for tr in (data.get("toolRequests") or []) if isinstance(tr, dict)
                    )
                for tr in requests:
                    name = tr.get("name") or "unknown_tool"
                    fp, cmd = _tool_path_and_cmd(tr.get("arguments") or tr.get("args"))
                    key = (str(name), fp, cmd)
                    if key in seen_tool_keys:
                        continue
                    seen_tool_keys.add(key)
                    events.append(
                        SessionEvent(
                            timestamp=ts,
                            tool_name=str(name),
                            file_path=fp,
                            bash_command=cmd,
                        )
                    )

    if not timestamps or not events:
        return None
    if project_path is None:
        project_path = path.parent.as_posix()

    return RawSession(
        session_id=session_id,
        project_path=project_path,
        source="copilot",
        start_time=min(timestamps),
        end_time=max(timestamps),
        events=events,
    )


class CopilotParser:
    name = "copilot"

    def iter_sessions(self, root: Path) -> list[RawSession]:
        root = Path(root)
        state_dir = root / "session-state" if (root / "session-state").exists() else root
        if not state_dir.exists():
            return []

        sessions: list[RawSession] = []
        for path in sorted(state_dir.glob("*/events.jsonl")):
            try:
                session = parse_events_file(path)
            except (OSError, UnicodeError):
                continue
            if session is not None:
                sessions.append(session)
        return sessions


register(CopilotParser())
