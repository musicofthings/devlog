"""
Parses Codex local rollout transcripts (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl)
into RawSession + SessionEvent records. Day slicing is handled by digest.slice_for_date.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from devlog.models import RawSession, SessionEvent
from devlog.sources.base import register

_SKIP_USER_PREFIXES = (
    "<environment_context",
    "<recommended_plugins",
    "<permissions",
    "<app-context",
    "<collaboration_mode",
    "<apps_instructions",
    "<plugins_instructions",
    "<skills_instructions",
    "<multi_agent_mode",
)

_PATH_RE = re.compile(
    r'(?:workdir|path|file_path|cwd)\s*[:=]\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_CMD_RE = re.compile(r'(?:command)\s*[:=]\s*["\']([^"\']+)["\']', re.IGNORECASE)


def _parse_timestamp(ts: object) -> datetime:
    if not isinstance(ts, str):
        raise ValueError("timestamp must be a string")
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _is_chrome_user_text(text: str) -> bool:
    stripped = text.lstrip()
    return any(stripped.startswith(p) for p in _SKIP_USER_PREFIXES)


def _extract_text_blocks(content: object) -> list[str]:
    texts: list[str] = []
    if isinstance(content, str) and content.strip():
        texts.append(content.strip())
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    return texts


def _tool_details(name: str, raw_input: object) -> tuple[str | None, str | None]:
    """Best-effort file_path and bash_command from tool input/arguments."""
    file_path: str | None = None
    bash_command: str | None = None

    if isinstance(raw_input, dict):
        file_path = (
            raw_input.get("path")
            or raw_input.get("file_path")
            or raw_input.get("workdir")
            or raw_input.get("cwd")
        )
        bash_command = raw_input.get("command") or raw_input.get("cmd")
        if isinstance(file_path, str):
            file_path = file_path
        else:
            file_path = None
        if isinstance(bash_command, str):
            bash_command = bash_command
        else:
            bash_command = None
        return file_path, bash_command

    if isinstance(raw_input, str) and raw_input.strip():
        try:
            parsed = json.loads(raw_input)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return _tool_details(name, parsed)
        m_path = _PATH_RE.search(raw_input)
        if m_path:
            file_path = m_path.group(1)
        m_cmd = _CMD_RE.search(raw_input)
        if m_cmd:
            bash_command = m_cmd.group(1)
        elif name in {"exec", "shell_command", "shell"} and not bash_command:
            # Keep a short snippet so digests still show activity.
            bash_command = raw_input.strip()[:200]
    return file_path, bash_command


def _clean_user_message(text: str, *, limit: int = 400) -> str | None:
    text = text.strip()
    if not text or _is_chrome_user_text(text):
        return None
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def parse_rollout_file(path: Path) -> RawSession | None:
    """Parse one Codex rollout JSONL into a RawSession, or None if unusable."""
    # File stem is a fallback id; a session_meta line overrides it below.
    session_id = path.stem

    project_path: str | None = None
    timestamps: list[datetime] = []
    events: list[SessionEvent] = []
    files_seen: list[str] = []
    # Cumulative totals last seen, used to convert total_token_usage to deltas.
    prev_totals = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}

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

            ts_raw = obj.get("timestamp")
            if not ts_raw:
                continue
            try:
                ts = _parse_timestamp(ts_raw)
            except (TypeError, ValueError):
                continue
            timestamps.append(ts)

            etype = obj.get("type")
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                payload = {}

            if etype == "session_meta":
                sid = payload.get("session_id") or payload.get("id")
                if isinstance(sid, str) and sid:
                    session_id = sid
                cwd = payload.get("cwd")
                if isinstance(cwd, str) and cwd:
                    project_path = cwd
                continue

            if etype == "event_msg":
                ptype = payload.get("type")
                if ptype == "user_message":
                    msg = payload.get("message")
                    if isinstance(msg, str):
                        cleaned = _clean_user_message(msg)
                        if cleaned:
                            events.append(SessionEvent(timestamp=ts, user_message=cleaned))
                elif ptype == "token_count":
                    info = payload.get("info")
                    if not isinstance(info, dict):
                        info = {}
                    last = info.get("last_token_usage")
                    total = info.get("total_token_usage")
                    if isinstance(last, dict):
                        events.append(
                            SessionEvent(
                                timestamp=ts,
                                tokens_in=_as_int(last.get("input_tokens")),
                                tokens_out=_as_int(last.get("output_tokens")),
                                tokens_cache_read=_as_int(last.get("cached_input_tokens")),
                            )
                        )
                        # Keep the cumulative baseline in sync even though this
                        # event was reported via last_token_usage, so a later
                        # total_token_usage-only event (format drift mid-file)
                        # still computes a correct delta instead of double
                        # counting tokens already reported here.
                        if isinstance(total, dict):
                            for key in prev_totals:
                                prev_totals[key] = _as_int(total.get(key))
                    elif isinstance(total, dict):
                        # total_token_usage is cumulative; summing it across
                        # events would inflate totals, so emit the delta.
                        deltas = {}
                        for key in prev_totals:
                            cur = _as_int(total.get(key))
                            deltas[key] = max(0, cur - prev_totals[key])
                            prev_totals[key] = cur
                        events.append(
                            SessionEvent(
                                timestamp=ts,
                                tokens_in=deltas["input_tokens"],
                                tokens_out=deltas["output_tokens"],
                                tokens_cache_read=deltas["cached_input_tokens"],
                            )
                        )
                continue

            if etype == "response_item":
                ptype = payload.get("type")
                if ptype == "message" and payload.get("role") == "user":
                    for text in _extract_text_blocks(payload.get("content")):
                        cleaned = _clean_user_message(text)
                        if cleaned:
                            events.append(SessionEvent(timestamp=ts, user_message=cleaned))
                elif ptype in {"custom_tool_call", "function_call"}:
                    name = payload.get("name") or "unknown_tool"
                    raw_in = payload.get("input", payload.get("arguments"))
                    fp, cmd = _tool_details(str(name), raw_in)
                    if fp:
                        files_seen.append(fp)
                    events.append(
                        SessionEvent(
                            timestamp=ts,
                            tool_name=str(name),
                            file_path=fp,
                            bash_command=cmd,
                        )
                    )

    if not timestamps:
        return None
    if project_path is None:
        project_path = files_seen[0] if files_seen else path.parent.as_posix()

    # Drop sessions that only have timestamps (meta/chrome) and no digestable events.
    if not events:
        return None

    return RawSession(
        session_id=session_id,
        project_path=project_path,
        source="codex",
        start_time=min(timestamps),
        end_time=max(timestamps),
        events=events,
    )


class CodexParser:
    name = "codex"

    def iter_sessions(self, root: Path) -> list[RawSession]:
        root = Path(root)
        sessions_dir = root / "sessions" if (root / "sessions").exists() else root
        if not sessions_dir.exists():
            return []

        sessions: list[RawSession] = []
        for path in sorted(sessions_dir.rglob("rollout-*.jsonl")):
            try:
                session = parse_rollout_file(path)
            except (OSError, UnicodeError):
                continue
            if session is not None:
                sessions.append(session)
        return sessions


register(CodexParser())
