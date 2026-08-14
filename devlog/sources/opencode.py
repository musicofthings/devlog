"""
Parses OpenCode session stores into RawSession records.

Prefers SQLite `opencode.db` (tables session / message / part). If the DB is
missing or empty, falls back to legacy JSON under storage/session/.
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
        elif val > 1e10:
            val /= 1000.0
        try:
            return datetime.fromtimestamp(val, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(ts, str) and ts.strip():
        raw = ts.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    return None


def _truncate(text: str, limit: int = 400) -> str:
    text = text.strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _load_json(value: object) -> dict | list | None:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, (dict, list)):
            return parsed
    return None


def _tool_path_and_cmd(raw_input: object) -> tuple[str | None, str | None]:
    parsed: object = raw_input
    if isinstance(raw_input, str):
        parsed = _load_json(raw_input)
    if not isinstance(parsed, dict):
        return None, None
    if isinstance(parsed.get("state"), dict):
        parsed = {**parsed, **parsed["state"]}
    if isinstance(parsed.get("input"), dict):
        parsed = {**parsed, **parsed["input"]}
    fp = (
        parsed.get("path")
        or parsed.get("file_path")
        or parsed.get("target_file")
        or parsed.get("directory")
    )
    cmd = parsed.get("command") or parsed.get("cmd")
    return (fp if isinstance(fp, str) else None), (cmd if isinstance(cmd, str) else None)


def _part_events(part: dict, fallback_ts: datetime | None) -> list[SessionEvent]:
    data = part
    nested = _load_json(part.get("data"))
    if isinstance(nested, dict):
        data = {**part, **nested}
    ts = (
        _parse_timestamp(data.get("time_created"))
        or _parse_timestamp(part.get("time_created"))
        or fallback_ts
    )
    if ts is None:
        return []
    ptype = data.get("type")
    events: list[SessionEvent] = []
    if ptype == "text":
        text = data.get("text") or data.get("content")
        if isinstance(text, str) and text.strip():
            events.append(SessionEvent(timestamp=ts, user_message=_truncate(text)))
    elif ptype in {"tool", "tool_use", "tool_call"}:
        name = data.get("tool") or data.get("name") or "unknown_tool"
        raw_in = data.get("state") if isinstance(data.get("state"), dict) else data
        fp, cmd = _tool_path_and_cmd(raw_in)
        if fp is None and cmd is None:
            fp, cmd = _tool_path_and_cmd(data)
        events.append(
            SessionEvent(timestamp=ts, tool_name=str(name), file_path=fp, bash_command=cmd)
        )
    return events


def _message_role(msg: dict) -> str:
    data = _load_json(msg.get("data"))
    if isinstance(data, dict) and isinstance(data.get("role"), str):
        return data["role"]
    role = msg.get("role")
    return role if isinstance(role, str) else ""


def _events_from_message(msg: dict, parts: list[dict]) -> list[SessionEvent]:
    ts = _parse_timestamp(msg.get("time_created"))
    nested = _load_json(msg.get("data"))
    if isinstance(nested, dict):
        time_obj = nested.get("time")
        created_raw = time_obj.get("created") if isinstance(time_obj, dict) else None
        ts = _parse_timestamp(created_raw) or ts
    events: list[SessionEvent] = []
    role = _message_role(msg)
    for part in parts:
        for ev in _part_events(part, ts):
            if ev.user_message and role != "user":
                continue
            events.append(ev)
    if not events and role == "user":
        content = None
        if isinstance(nested, dict):
            content = nested.get("content") or nested.get("text")
        content = content or msg.get("content")
        if isinstance(content, str) and content.strip() and ts is not None:
            events.append(SessionEvent(timestamp=ts, user_message=_truncate(content)))
    return events


def _open_readonly(db: Path) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=1.0)
    except sqlite3.Error:
        return None


def _table_names(cur: sqlite3.Cursor) -> set[str]:
    rows = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows if r and isinstance(r[0], str)}


def _sessions_from_sqlite(db: Path) -> list[RawSession]:
    con = _open_readonly(db)
    if con is None:
        return []
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        tables = _table_names(cur)
        if not {"session", "message", "part"} <= tables:
            return []
        sessions_out: list[RawSession] = []
        for sess in cur.execute("SELECT * FROM session").fetchall():
            sess_d = dict(sess)
            sid = str(sess_d.get("id") or "")
            if not sid:
                continue
            directory = sess_d.get("directory") or sess_d.get("cwd") or ""
            created = _parse_timestamp(sess_d.get("time_created"))
            updated = _parse_timestamp(sess_d.get("time_updated"))
            messages = [
                dict(r)
                for r in cur.execute("SELECT * FROM message WHERE session_id = ?", (sid,))
            ]
            parts_by_msg: dict[str, list[dict]] = {}
            for part in cur.execute("SELECT * FROM part WHERE session_id = ?", (sid,)):
                pd = dict(part)
                mid = str(pd.get("message_id") or "")
                parts_by_msg.setdefault(mid, []).append(pd)
            events: list[SessionEvent] = []
            timestamps: list[datetime] = []
            for msg in messages:
                mid = str(msg.get("id") or "")
                for ev in _events_from_message(msg, parts_by_msg.get(mid, [])):
                    events.append(ev)
                    timestamps.append(ev.timestamp)
            if created:
                timestamps.append(created)
            if updated:
                timestamps.append(updated)
            if not events or not timestamps:
                continue
            project = str(directory).replace("\\", "/").rstrip("/") or db.parent.as_posix()
            sessions_out.append(
                RawSession(
                    session_id=sid,
                    project_path=project,
                    source="opencode",
                    start_time=min(timestamps),
                    end_time=max(timestamps),
                    events=events,
                )
            )
        return sessions_out
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _legacy_part_list(msg: dict) -> list[dict]:
    parts = msg.get("parts") or msg.get("content")
    if isinstance(parts, list):
        return [p for p in parts if isinstance(p, dict)]
    if isinstance(msg.get("content"), str):
        return [{"type": "text", "text": msg["content"]}]
    return []


def _sessions_from_legacy_json(root: Path) -> list[RawSession]:
    storage = root / "storage" / "session"
    if not storage.exists():
        return []
    out: list[RawSession] = []
    for path in sorted(storage.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        sid = str(data.get("id") or path.stem)
        directory = data.get("directory") or data.get("cwd") or path.parent.as_posix()
        time_obj = data.get("time") if isinstance(data.get("time"), dict) else {}
        created = _parse_timestamp(time_obj.get("created") or data.get("time_created"))
        messages = data.get("messages")
        if not isinstance(messages, list):
            continue
        events: list[SessionEvent] = []
        timestamps: list[datetime] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            msg_ts = None
            tinfo = msg.get("time")
            if isinstance(tinfo, dict):
                msg_ts = _parse_timestamp(tinfo.get("created"))
            msg_ts = msg_ts or created
            wrapped = {
                "role": msg.get("role"),
                "data": json.dumps({"role": msg.get("role"), "content": msg.get("content")}),
                "time_created": None,
                "content": msg.get("content"),
            }
            # Preserve numeric created if present
            if msg_ts is not None:
                wrapped["time_created"] = int(msg_ts.timestamp() * 1000)
            for ev in _events_from_message(wrapped, _legacy_part_list(msg)):
                events.append(ev)
                timestamps.append(ev.timestamp)
        if not events:
            continue
        if created:
            timestamps.append(created)
        if not timestamps:
            continue
        out.append(
            RawSession(
                session_id=sid,
                project_path=str(directory).replace("\\", "/").rstrip("/"),
                source="opencode",
                start_time=min(timestamps),
                end_time=max(timestamps),
                events=events,
            )
        )
    return out


def _find_db(root: Path) -> Path | None:
    for candidate in (root / "opencode.db", root / "data" / "opencode.db"):
        if candidate.is_file():
            return candidate
    return None


class OpenCodeParser:
    name = "opencode"

    def iter_sessions(self, root: Path) -> list[RawSession]:
        root = Path(root)
        if not root.exists():
            return []
        db = _find_db(root)
        if db is not None:
            found = _sessions_from_sqlite(db)
            if found:
                return found
        return _sessions_from_legacy_json(root)


register(OpenCodeParser())
