"""
Parses Claude Code local session transcripts (~/.claude/projects/*/*.jsonl)
into a structured per-day digest: projects touched, files edited, tools used,
tokens consumed, and the user's own task descriptions (the real signal for
"what did I work on today").

Claude Code JSONL format reference (as of mid-2026):
  - One file per session: ~/.claude/projects/<url-encoded-project-path>/<session-uuid>.jsonl
  - One JSON object per line, one of: "user", "assistant", "tool_result", "system"
  - assistant messages carry message.content: list of blocks, each either
    {"type": "text", "text": ...} or {"type": "tool_use", "name": ..., "input": {...}}
  - assistant messages carry message.usage: token counts (input/output/cache)
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote


def decode_project_path(encoded_dir_name: str) -> str:
    """Claude Code encodes the absolute project path into the folder name by
    replacing path separators with '-'. This is lossy (can't perfectly restore
    dashes that were part of real folder names), so we just make it readable."""
    name = encoded_dir_name
    if name.startswith("-"):
        name = name[1:]
    return "/" + name.replace("-", "/")


@dataclass
class SessionDigest:
    session_id: str
    project_path: str
    start_time: datetime
    end_time: datetime
    user_messages: list[str] = field(default_factory=list)
    tool_calls: dict[str, int] = field(default_factory=dict)
    files_touched: set[str] = field(default_factory=set)
    bash_commands: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache_read: int = 0

    @property
    def duration_minutes(self) -> float:
        return max(0.0, (self.end_time - self.start_time).total_seconds() / 60.0)


def _parse_timestamp(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def parse_session_file(path: Path) -> SessionDigest | None:
    """Parse a single .jsonl session transcript into a SessionDigest.
    Returns None if the file has no usable events (empty/corrupt session)."""
    project_dir = path.parent.name
    project_path = decode_project_path(project_dir)
    session_id = path.stem

    timestamps: list[datetime] = []
    user_messages: list[str] = []
    tool_calls: dict[str, int] = {}
    files_touched: set[str] = set()
    bash_commands: list[str] = []
    tokens_in = tokens_out = tokens_cache_read = 0

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate partially-written trailing lines

            ts_raw = event.get("timestamp")
            if ts_raw:
                try:
                    timestamps.append(_parse_timestamp(ts_raw))
                except ValueError:
                    pass

            etype = event.get("type")
            msg = event.get("message", {})

            if etype == "user":
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    user_messages.append(content.strip())
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            user_messages.append(block.get("text", "").strip())

            elif etype == "assistant":
                usage = msg.get("usage", {})
                tokens_in += usage.get("input_tokens", 0)
                tokens_out += usage.get("output_tokens", 0)
                tokens_cache_read += usage.get("cache_read_input_tokens", 0)

                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            name = block.get("name", "unknown_tool")
                            tool_calls[name] = tool_calls.get(name, 0) + 1
                            tool_input = block.get("input", {})
                            fp = tool_input.get("file_path")
                            if fp:
                                files_touched.add(fp)
                            cmd = tool_input.get("command")
                            if cmd:
                                bash_commands.append(cmd)

    if not timestamps:
        return None

    return SessionDigest(
        session_id=session_id,
        project_path=project_path,
        start_time=min(timestamps),
        end_time=max(timestamps),
        user_messages=user_messages,
        tool_calls=tool_calls,
        files_touched=files_touched,
        bash_commands=bash_commands,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_cache_read=tokens_cache_read,
    )


def find_sessions_for_date(claude_root: Path, target_date: str) -> list[SessionDigest]:
    """Scan every project folder under claude_root/projects for sessions
    that had any activity on target_date (YYYY-MM-DD, UTC)."""
    sessions: list[SessionDigest] = []
    projects_dir = claude_root / "projects"
    if not projects_dir.exists():
        return sessions

    for project_folder in projects_dir.iterdir():
        if not project_folder.is_dir():
            continue
        for session_file in project_folder.glob("*.jsonl"):
            digest = parse_session_file(session_file)
            if digest is None:
                continue
            if digest.start_time.strftime("%Y-%m-%d") == target_date or \
               digest.end_time.strftime("%Y-%m-%d") == target_date:
                sessions.append(digest)

    return sorted(sessions, key=lambda s: s.start_time)


if __name__ == "__main__":
    # Quick self-test against the synthetic sample data
    sample_root = Path(__file__).parent / "sample_data"
    fake_claude_root = sample_root  # sample_data/ mimics ~/.claude/ layout directly under "projects"
    # sample_data IS the projects dir in our synthetic layout, so wrap it:
    sessions = []
    for project_folder in sample_root.iterdir():
        if project_folder.is_dir():
            for session_file in project_folder.glob("*.jsonl"):
                d = parse_session_file(session_file)
                if d:
                    sessions.append(d)

    for s in sessions:
        print(f"\n--- Session {s.session_id} ({s.project_path}) ---")
        print(f"  {s.start_time} → {s.end_time}  ({s.duration_minutes:.1f} min)")
        print(f"  Tasks: {s.user_messages}")
        print(f"  Tool calls: {s.tool_calls}")
        print(f"  Files touched: {s.files_touched}")
        print(f"  Bash commands: {s.bash_commands}")
        print(f"  Tokens: in={s.tokens_in} out={s.tokens_out} cache_read={s.tokens_cache_read}")
