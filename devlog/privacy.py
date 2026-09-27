"""Redaction helpers for transcript-derived text leaving the local pipeline."""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

_SECRET_PATTERNS = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
_BEARER_RE = re.compile(
    r"(?i)(\bauthorization\s*[:=]\s*bearer\s+)[^\s\"']+"
)
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD))\s*=\s*([^\s,;]+)"
)



@lru_cache(maxsize=64)
def _compile_user_pattern(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def redact_sensitive_text(text: str, extra_patterns: Iterable[str] = ()) -> str:
    """Best-effort removal of common credentials and the user's home path.

    `extra_patterns` are user regexes from config `redact_patterns` (MRNs,
    sample IDs, ...); every match becomes [REDACTED].
    """
    redacted = text
    for pattern in extra_patterns:
        redacted = _compile_user_pattern(pattern).sub("[REDACTED]", redacted)
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    redacted = _BEARER_RE.sub(r"\1[REDACTED_SECRET]", redacted)
    redacted = _ASSIGNMENT_RE.sub(r"\1=[REDACTED_SECRET]", redacted)

    home = str(Path.home())
    for variant in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
        if variant:
            redacted = re.sub(re.escape(variant), "~", redacted, flags=re.IGNORECASE)
    return redacted
