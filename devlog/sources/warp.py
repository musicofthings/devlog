"""
Parses Warp agent conversations from the local SQLite store
(%LOCALAPPDATA%/warp/Warp/data/warp.sqlite).

Tables: agent_conversations, ai_queries, agent_tasks.
Zero rows is not an error — Warp may keep transcripts in the cloud.
Day slicing is handled by digest.slice_for_date.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from devlog.models import RawSession, SessionEvent
from devlog.sources.base import register


def _parse_timestamp(ts: object) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        val = float(ts)
        if val > 1e12:
            val /= 1000.0
        try:
            return datetime.fromtimestamp(val, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(ts, str) or not ts.strip():
        return None
    raw = ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _truncate(text: str, limit: int = 400) -> str:
    text = text.strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _load_json(value: object) -> dict | list | None:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeError:
            return None
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, (dict, list)):
            return parsed
    return None


def _tool_path_and_cmd(raw_input: object) -> tuple[str | None, str | None]:
    if not isinstance(raw_input, dict):
        return None, None
    fp = raw_input.get("path") or raw_input.get("file_path") or raw_input.get("target_file")
    cmd = raw_input.get("command") or raw_input.get("cmd")
    return (fp if isinstance(fp, str) else None), (cmd if isinstance(cmd, str) else None)


def _events_from_conversation_data(
    data: object, fallback_ts: datetime | None
) -> list[SessionEvent]:
    parsed = _load_json(data)
    if parsed is None:
        return []
    messages = []
    if isinstance(parsed, dict):
        messages = parsed.get("messages") or parsed.get("exchanges") or []
    elif isinstance(parsed, list):
        messages = parsed
    events: list[SessionEvent] = []
    if not isinstance(messages, list):
        return events
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        ts = (
            _parse_timestamp(msg.get("ts") or msg.get("timestamp") or msg.get("time"))
            or fallback_ts
        )
        if ts is None:
            continue
        role = msg.get("role")
        text = msg.get("text") or msg.get("content") or msg.get("input")
        if role == "user" and isinstance(text, str) and text.strip():
            events.append(SessionEvent(timestamp=ts, user_message=_truncate(text)))
        for call in msg.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            name = call.get("name") or "unknown_tool"
            fp, cmd = _tool_path_and_cmd(call.get("input") or call.get("arguments") or {})
            events.append(
                SessionEvent(timestamp=ts, tool_name=str(name), file_path=fp, bash_command=cmd)
            )
    return events


def _find_db(root: Path) -> Path | None:
    for candidate in (root / "data" / "warp.sqlite", root / "warp.sqlite"):
        if candidate.is_file():
            return candidate
    return None


def _open_readonly(db: Path) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=1.0)
    except sqlite3.Error:
        return None


class WarpParser:
    name = "warp"

    def iter_sessions(self, root: Path) -> list[RawSession]:
        root = Path(root)
        db = _find_db(root)
        if db is None:
            return []
        con = _open_readonly(db)
        if con is None:
            return []
        con.row_factory = sqlite3.Row
        try:
            cur = con.cursor()
            tables = {
                r[0]
                for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            grouped: dict[str, dict] = {}

            if "ai_queries" in tables:
                for row in cur.execute("SELECT * FROM ai_queries"):
                    d = dict(row)
                    cid = str(d.get("conversation_id") or d.get("exchange_id") or "")
                    if not cid:
                        continue
                    bucket = grouped.setdefault(
                        cid,
                        {"queries": [], "conversation": None, "tasks": []},
                    )
                    bucket["queries"].append(d)

            if "agent_conversations" in tables:
                for row in cur.execute("SELECT * FROM agent_conversations"):
                    d = dict(row)
                    cid = str(d.get("conversation_id") or d.get("id") or "")
                    if not cid:
                        continue
                    bucket = grouped.setdefault(
                        cid,
                        {"queries": [], "conversation": None, "tasks": []},
                    )
                    bucket["conversation"] = d

            if "agent_tasks" in tables:
                for row in cur.execute("SELECT * FROM agent_tasks"):
                    d = dict(row)
                    cid = str(d.get("conversation_id") or "")
                    if not cid:
                        continue
                    bucket = grouped.setdefault(
                        cid,
                        {"queries": [], "conversation": None, "tasks": []},
                    )
                    bucket["tasks"].append(d)

            sessions: list[RawSession] = []
            for cid, bucket in grouped.items():
                events: list[SessionEvent] = []
                timestamps: list[datetime] = []
                project_path: str | None = None
                for q in bucket["queries"]:
                    ts = _parse_timestamp(q.get("start_ts"))
                    if ts is None:
                        continue
                    timestamps.append(ts)
                    wd = q.get("working_directory")
                    if isinstance(wd, str) and wd and project_path is None:
                        project_path = wd.replace("\\", "/").rstrip("/")
                    text = q.get("input")
                    if isinstance(text, str) and text.strip():
                        events.append(SessionEvent(timestamp=ts, user_message=_truncate(text)))
                conv = bucket["conversation"]
                conv_ts = None
                if isinstance(conv, dict):
                    conv_ts = _parse_timestamp(conv.get("last_modified_at"))
                    if conv_ts:
                        timestamps.append(conv_ts)
                    fallback = conv_ts or (timestamps[-1] if timestamps else None)
                    for ev in _events_from_conversation_data(
                        conv.get("conversation_data"), fallback
                    ):
                        events.append(ev)
                        timestamps.append(ev.timestamp)
                for task in bucket["tasks"]:
                    ts = _parse_timestamp(task.get("last_modified_at")) or (
                        timestamps[-1] if timestamps else None
                    )
                    parsed = _load_json(task.get("task"))
                    if not isinstance(parsed, dict) or ts is None:
                        continue
                    name = parsed.get("name") or parsed.get("tool") or "agent_task"
                    fp, cmd = _tool_path_and_cmd(parsed.get("input") or parsed)
                    events.append(
                        SessionEvent(
                            timestamp=ts,
                            tool_name=str(name),
                            file_path=fp,
                            bash_command=cmd,
                        )
                    )
                    timestamps.append(ts)
                if not events or not timestamps:
                    continue
                sessions.append(
                    RawSession(
                        session_id=cid,
                        project_path=project_path or root.as_posix(),
                        source="warp",
                        start_time=min(timestamps),
                        end_time=max(timestamps),
                        events=events,
                    )
                )
            return sessions
        except sqlite3.Error:
            return []
        finally:
            con.close()


register(WarpParser())
