from __future__ import annotations

from pathlib import Path
from typing import Protocol

from devlog.models import RawSession


class SourceParser(Protocol):
    name: str

    def iter_sessions(self, root: Path) -> list[RawSession]: ...


REGISTRY: dict[str, SourceParser] = {}


def register(parser: SourceParser) -> SourceParser:
    REGISTRY[parser.name] = parser
    return parser


def get_sources(names: list[str]) -> list[SourceParser]:
    missing = [n for n in names if n not in REGISTRY]
    if missing:
        known = ", ".join(sorted(REGISTRY)) or "(none yet)"
        raise KeyError(f"Unknown source(s): {', '.join(missing)}. Known: {known}")
    return [REGISTRY[n] for n in names]
