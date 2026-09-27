"""Extract open threads ("Next steps", TODOs) from an assistant's recap.

Agents usually end a turn with a summary and a list of follow-ups. Those
follow-ups are the best record of what was left undone, so the vault turns
them into checkboxes on day notes and an "Open threads" list on project hubs.
Parsers call `extract_threads` at the end of each assistant turn and keep
only the result, not the (large) assistant text.
"""

from __future__ import annotations

import re

MAX_THREADS = 8
THREAD_CHARS = 200

_HEADING_RE = re.compile(
    r"^\s{0,3}(?:#{1,6}\s*)?(?:\*\*|__)?\s*"
    r"(?:suggested\s+|recommended\s+|possible\s+|remaining\s+)?"
    r"(?:next\s+steps?|follow[- ]?ups?|to-?dos?|open\s+(?:questions|items|issues|threads)|"
    r"what'?s\s+left|remaining(?:\s+work)?|not\s+(?:yet\s+)?done|left\s+to\s+do|"
    r"pending(?:\s+items)?|outstanding(?:\s+items)?)"
    r"\s*(?:\*\*|__)?\s*:?\s*(?:\*\*|__)?\s*$",
    re.IGNORECASE,
)
_ITEM_RE = re.compile(r"^\s{0,6}(?:[-*+]|\d{1,2}[.)])\s+(?:\[[ ]\]\s+)?(?P<text>\S.*)$")
_UNCHECKED_RE = re.compile(r"^\s{0,6}[-*+]\s+\[ \]\s+(?P<text>\S.*)$")
_NEW_SECTION_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s|\*\*[^*]+\*\*\s*:?\s*$)")


def _clean(text: str) -> str:
    text = re.sub(r"\*\*|__|`", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [label](url) -> label
    text = re.sub(r"\s+", " ", text).strip().rstrip(":")
    if len(text) > THREAD_CHARS:
        text = text[: THREAD_CHARS - 1].rstrip() + "…"
    return text


def extract_threads(text: str | None) -> list[str]:
    """Bullets under a next-steps style heading, plus any `- [ ]` items."""
    if not text:
        return []
    found: list[str] = []
    lines = text.replace("\r\n", "\n").split("\n")
    in_section = False
    for line in lines:
        if _HEADING_RE.match(line):
            in_section = True
            continue
        if in_section:
            item = _ITEM_RE.match(line)
            if item:
                found.append(item.group("text"))
                continue
            if not line.strip():
                continue
            if line.startswith((" ", "\t")) and not _NEW_SECTION_RE.match(line):
                continue  # wrapped continuation of the previous bullet
            in_section = False
        unchecked = _UNCHECKED_RE.match(line)
        if unchecked:
            found.append(unchecked.group("text"))

    out: list[str] = []
    for raw in found:
        cleaned = _clean(raw)
        if cleaned and cleaned not in out:
            out.append(cleaned)
        if len(out) == MAX_THREADS:
            break
    return out


class TurnRecap:
    """Remembers the latest assistant text of the current turn.

    `flush()` at every real user message and at end of file yields the
    threads of the turn that just ended (with its timestamp), if any.
    """

    def __init__(self) -> None:
        self._pending: tuple[object, str] | None = None

    def assistant(self, ts, text: str) -> None:
        if text and text.strip():
            self._pending = (ts, text)

    def flush(self):
        if self._pending is None:
            return None
        ts, text = self._pending
        self._pending = None
        threads = extract_threads(text)
        return (ts, threads) if threads else None
