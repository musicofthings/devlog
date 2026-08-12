"""Soft-hide bookkeeping: dates excluded from the public feed but kept in posts/."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

HIDDEN_FILENAME = ".devlog-hidden.json"


def hidden_path(repo: Path) -> Path:
    return Path(repo) / HIDDEN_FILENAME


def load_hidden_dates(repo: Path) -> set[str]:
    path = hidden_path(repo)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    dates = data.get("dates", [])
    if not isinstance(dates, list):
        return set()
    return {str(item) for item in dates if isinstance(item, str)}


def save_hidden_dates(repo: Path, dates: set[str]) -> Path:
    path = hidden_path(repo)
    ordered = sorted(dates)
    body = {"dates": ordered}
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def is_hidden(repo: Path, day: date | str) -> bool:
    iso = day.isoformat() if isinstance(day, date) else day
    return iso in load_hidden_dates(repo)


def hide_date(repo: Path, day: date) -> Path:
    dates = load_hidden_dates(repo)
    dates.add(day.isoformat())
    return save_hidden_dates(repo, dates)


def unhide_date(repo: Path, day: date) -> Path:
    dates = load_hidden_dates(repo)
    dates.discard(day.isoformat())
    if dates:
        return save_hidden_dates(repo, dates)
    path = hidden_path(repo)
    if path.exists():
        path.unlink()
    return path
