"""Redaction helpers for transcript-derived text leaving the local pipeline."""

from __future__ import annotations

import re
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


# Ready-made identifier patterns, switched on with `redact_presets` in config.toml.
# Starting points for clinical work, not a de-identification guarantee: add
# your own sample-ID and accession formats to `redact_patterns`.
REDACT_PRESETS: dict[str, list[str]] = {
    "mrn": [r"(?i)\b(?:MRN|medical record(?: number| no\.?| #)?)[\s:#-]*\d[\d-]{3,}\b"],
    "dob": [r"(?i)\b(?:DOB|date of birth|birth ?date)[\s:]*"
            r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b"],
    "ssn": [r"\b\d{3}-\d{2}-\d{4}\b"],
    "phone": [r"(?<![\w-])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\w-])"],
    "email": [r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"],
}
REDACT_PRESETS["clinical"] = [p for name in ("mrn", "dob", "ssn", "phone", "email")
                              for p in REDACT_PRESETS[name]]

# User patterns from `redact_patterns` in config.toml (MRNs, sample IDs, ...).
_user_patterns: list[re.Pattern[str]] = []
# Matches of the user patterns since configure_redaction (for --explain).
_user_hits = [0]


def preset_patterns(presets: list[str] | None) -> list[str]:
    out: list[str] = []
    for name in presets or []:
        for pattern in REDACT_PRESETS[name]:
            if pattern not in out:
                out.append(pattern)
    return out


def configure_redaction(patterns: list[str] | None, presets: list[str] | None = None) -> None:
    """Install the user's extra redaction regexes and presets (replaces any previous set)."""
    _user_patterns[:] = [re.compile(p) for p in [*(patterns or []), *preset_patterns(presets)]]
    _user_hits[0] = 0


def user_redaction_count() -> int:
    """How many user-pattern matches were redacted since configure_redaction()."""
    return _user_hits[0]


def redact_sensitive_text(text: str) -> str:
    """Best-effort removal of credentials, the home path, and user patterns."""
    redacted = text
    for pattern in _user_patterns:
        redacted, n = pattern.subn("[REDACTED]", redacted)
        _user_hits[0] += n
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    redacted = _BEARER_RE.sub(r"\1[REDACTED_SECRET]", redacted)
    redacted = _ASSIGNMENT_RE.sub(r"\1=[REDACTED_SECRET]", redacted)

    home = str(Path.home())
    for variant in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
        if variant:
            redacted = re.sub(re.escape(variant), "~", redacted, flags=re.IGNORECASE)
    return redacted


def secret_matches(text: str) -> list[str]:
    """Credentials in `text` (API keys, tokens, bearer headers, KEY=value)."""
    found = [m.group(0) for pattern in _SECRET_PATTERNS for m in pattern.finditer(text)]
    found += [m.group(0) for m in _BEARER_RE.finditer(text)]
    found += [m.group(0) for m in _ASSIGNMENT_RE.finditer(text)
              if not m.group(2).startswith("[REDACTED")]
    return [f for f in found if "[REDACTED" not in f]


def identifier_matches(text: str) -> list[str]:
    """Matches of `redact_patterns` and `redact_presets` (as configured)."""
    return [m.group(0) for pattern in _user_patterns for m in pattern.finditer(text)
            if "[REDACTED" not in m.group(0)]
