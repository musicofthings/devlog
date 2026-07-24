from __future__ import annotations
from pathlib import Path
from devlog.models import RawSession
from devlog.sources.base import register

class CursorParser:
    name = "cursor"
    def iter_sessions(self, root: Path) -> list[RawSession]:
        return []

register(CursorParser())
