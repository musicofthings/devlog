"""Small persisted status file recording the last publish/delete, for site display."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

STATUS_FILENAME = ".devlog-status.json"


def status_path(repo: Path) -> Path:
    return Path(repo) / STATUS_FILENAME


def load_status(repo: Path) -> dict:
    path = status_path(repo)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record_event(repo: Path, *, event: str, date: str, at: str | None = None) -> Path:
    """Record a publish/delete/hide/unhide event. date is the post's YYYY-MM-DD."""
    data = load_status(repo)
    data[f"last_{event}_date"] = date
    data[f"last_{event}_at"] = at or datetime.now(UTC).isoformat(timespec="seconds")
    path = status_path(repo)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
