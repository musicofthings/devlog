from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from devlog.models import RawSession


class SourceParser(Protocol):
    name: str

    def iter_sessions(
        self, root: Path, since: datetime | None = None
    ) -> list[RawSession]: ...


REGISTRY: dict[str, SourceParser] = {}


def modified_since(path: Path, since: datetime | None) -> bool:
    """Whether a log file may hold events at/after `since`.

    Session logs are append-only, so a file last modified before `since`
    cannot contain later events. Scanning only recent files keeps a nightly
    publish fast no matter how much history has piled up.
    """
    if since is None:
        return True
    try:
        return path.stat().st_mtime >= since.timestamp()
    except OSError:
        return True


def register(parser: SourceParser) -> SourceParser:
    REGISTRY[parser.name] = parser
    return parser


def get_sources(names: list[str]) -> list[SourceParser]:
    missing = [n for n in names if n not in REGISTRY]
    if missing:
        known = ", ".join(sorted(REGISTRY)) or "(none yet)"
        raise KeyError(f"Unknown source(s): {', '.join(missing)}. Known: {known}")
    return [REGISTRY[n] for n in names]
