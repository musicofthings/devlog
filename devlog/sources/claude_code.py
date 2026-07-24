"""
Parses Claude Code local session transcripts (~/.claude/projects/*/*.jsonl)
into RawSession + SessionEvent records. Day slicing is handled separately by
devlog.digest; this module only knows how to read Claude Code's on-disk format.

Claude Code JSONL format reference (as of mid-2026):
  - One file per session: ~/.claude/projects/<url-encoded-project-path>/<session-uuid>.jsonl
  - One JSON object per line, one of: "user", "assistant", "tool_result", "system"
  - Some events carry a "cwd" field with the absolute working directory
  - assistant messages carry message.content: list of blocks, each either
    {"type": "text", "text": ...} or {"type": "tool_use", "name": ..., "input": {...}}
  - assistant messages carry message.usage: token counts (input/output/cache)
"""

from __future__ import annotations
import json
import posixpath
from datetime import datetime
from pathlib import Path

from devlog.models import RawSession, SessionEvent
from devlog.sources.base import register


def decode_project_path(encoded_dir_name: str) -> str:
    """Claude Code encodes the absolute project path into the folder name by
    replacing path separators with '-'. This is lossy (can't perfectly restore
    dashes that were part of real folder names), so we just make it readable."""
    name = encoded_dir_name
    if name.startswith("-"):
        name = name[1:]
    return "/" + name.replace("-", "/")


def _is_ancestor_or_equal(root: str, other: str) -> bool:
    """True if `root` is an ancestor of (or equal to) `other` (posix paths)."""
    try:
        return posixpath.commonpath([root, other]) == posixpath.normpath(root)
    except ValueError:
        return False


def resolve_project_path(cwd: str | None, files: list[str], folder_name: str) -> str:
    """Resolve a session's project path with priority:
    1. `cwd` seen on a transcript event (authoritative)
    2. Best-effort common root of touched tool file paths -- but only when it
       is at least as high up the tree as the folder-name decode. Tool calls
       often only touch a subdirectory of the project (e.g. a `parsers/`
       package), which would make the common root a strict subdirectory of
       the real project root and *worse* than the folder decode.
    3. Lossy folder-name decode as last resort
    """
    if cwd:
        return cwd
    folder_decoded = decode_project_path(folder_name)
    if files:
        try:
            common = posixpath.commonpath([posixpath.dirname(f) for f in files])
        except ValueError:
            common = ""
        if common and _is_ancestor_or_equal(common, folder_decoded):
            return common
    return folder_decoded


def _parse_timestamp(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def parse_session_file(path: Path) -> RawSession | None:
    """Parse a single .jsonl session transcript into a RawSession.
    Returns None if the file has no usable events (empty/corrupt session).
    Malformed JSON lines are skipped rather than failing the whole file."""
    folder_name = path.parent.name
    session_id = path.stem

    timestamps: list[datetime] = []
    events: list[SessionEvent] = []
    cwd: str | None = None
    files_seen: list[str] = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate malformed/partially-written lines

            if not isinstance(event, dict):
                continue

            if cwd is None:
                ev_cwd = event.get("cwd")
                if ev_cwd:
                    cwd = ev_cwd

            ts_raw = event.get("timestamp")
            if not ts_raw:
                continue
            try:
                ts = _parse_timestamp(ts_raw)
            except ValueError:
                continue
            timestamps.append(ts)

            etype = event.get("type")
            msg = event.get("message")
            if not isinstance(msg, dict):
                msg = {}

            if etype == "user":
                content = msg.get("content")
                texts: list[str] = []
                if isinstance(content, str) and content.strip():
                    texts.append(content.strip())
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                texts.append(text)
                for text in texts:
                    events.append(SessionEvent(timestamp=ts, user_message=text))

            elif etype == "assistant":
                usage = msg.get("usage") or {}
                tokens_in = usage.get("input_tokens", 0)
                tokens_out = usage.get("output_tokens", 0)
                tokens_cache_read = usage.get("cache_read_input_tokens", 0)

                content = msg.get("content", [])
                tool_blocks: list[dict] = []
                if isinstance(content, list):
                    tool_blocks = [
                        block for block in content
                        if isinstance(block, dict) and block.get("type") == "tool_use"
                    ]

                if not tool_blocks:
                    if tokens_in or tokens_out or tokens_cache_read:
                        events.append(SessionEvent(
                            timestamp=ts,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                            tokens_cache_read=tokens_cache_read,
                        ))
                else:
                    # Token usage is reported once per assistant turn, not per
                    # tool call, so attach it only to the first tool event to
                    # avoid multiplying totals when a turn makes several calls.
                    for i, block in enumerate(tool_blocks):
                        name = block.get("name", "unknown_tool")
                        tool_input = block.get("input") or {}
                        fp = tool_input.get("file_path")
                        cmd = tool_input.get("command")
                        if fp:
                            files_seen.append(fp)
                        events.append(SessionEvent(
                            timestamp=ts,
                            tool_name=name,
                            file_path=fp,
                            bash_command=cmd,
                            tokens_in=tokens_in if i == 0 else 0,
                            tokens_out=tokens_out if i == 0 else 0,
                            tokens_cache_read=tokens_cache_read if i == 0 else 0,
                        ))

    if not timestamps:
        return None

    project_path = resolve_project_path(cwd, files_seen, folder_name)

    return RawSession(
        session_id=session_id,
        project_path=project_path,
        source="claude_code",
        start_time=min(timestamps),
        end_time=max(timestamps),
        events=events,
    )


class ClaudeCodeParser:
    name = "claude_code"

    def iter_sessions(self, root: Path) -> list[RawSession]:
        root = Path(root)
        projects_dir = root / "projects" if (root / "projects").exists() else root

        sessions: list[RawSession] = []
        if not projects_dir.exists():
            return sessions

        for project_folder in sorted(projects_dir.iterdir()):
            if not project_folder.is_dir():
                continue
            for session_file in sorted(project_folder.glob("*.jsonl")):
                session = parse_session_file(session_file)
                if session is not None:
                    sessions.append(session)

        return sessions


register(ClaudeCodeParser())
