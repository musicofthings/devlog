"""
Antigravity CLI/IDE transcripts.

Conversations under ~/.gemini/antigravity-cli and antigravity-ide are typically
protobuf/encrypted. This plugin does not decode those stores. If plaintext
.jsonl files appear, they are parsed; otherwise the source yields no sessions
(missing root is skipped by the CLI like every other plugin).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from devlog.models import RawSession
from devlog.sources.base import modified_since, register
from devlog.sources.vitreous import parse_session_file


class AntigravityParser:
    name = "antigravity"

    def iter_sessions(self, root: Path, since: datetime | None = None) -> list[RawSession]:
        root = Path(root)
        if not root.exists():
            return []
        sessions: list[RawSession] = []
        for path in sorted(root.rglob("*.jsonl")):
            if not modified_since(path, since):
                continue
            if not path.is_file():
                continue
            try:
                session = parse_session_file(path, source="antigravity")
            except (OSError, UnicodeError):
                continue
            if session is not None:
                sessions.append(session)
        return sessions


register(AntigravityParser())
