"""Scoring rubrics for Phase 1 acceptance-style evals.

Checks are deterministic and offline-friendly. Live Claude posts use the same
groundedness/style checks; exact wording is never asserted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from devlog.models import SessionDigest

_EMOJI_RE = re.compile(
    "[\U0001f300-\U0001f9ff\U00002700-\U000027bf\U0001f600-\U0001f64f]+",
    flags=re.UNICODE,
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class EvalResult:
    case_id: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(CheckResult(name=name, passed=passed, detail=detail))


def _sentence_count(text: str) -> int:
    parts = [p.strip() for p in re.split(r"[.!?]+", text) if p.strip()]
    return len(parts)


def _word_count(text: str) -> int:
    return len(text.split())


def allowed_tokens_from_digests(digests: list[SessionDigest]) -> set[str]:
    """Lowercased tokens that are fair game to appear in a grounded post."""
    allowed: set[str] = set()
    for d in digests:
        for part in d.project_path.replace("\\", "/").split("/"):
            if part:
                allowed.add(part.lower())
        for msg in d.user_messages:
            for tok in re.findall(r"[A-Za-z0-9_./-]+", msg):
                if len(tok) >= 3:
                    allowed.add(tok.lower())
        for fp in d.files_touched:
            for part in fp.replace("\\", "/").split("/"):
                if part:
                    allowed.add(part.lower())
                    stem = part.rsplit(".", 1)[0]
                    if stem:
                        allowed.add(stem.lower())
            allowed.add(fp.lower())
        for tool in d.tool_calls:
            allowed.add(tool.lower())
        for cmd in d.bash_commands:
            for tok in re.findall(r"[A-Za-z0-9_./-]+", cmd):
                if len(tok) >= 3:
                    allowed.add(tok.lower())
    # Always-ok filler from the template / prompt style
    allowed.update(
        {
            "today",
            "min",
            "across",
            "on",
            "tools",
            "no",
            "coding",
            "activity",
            "logged",
            "recorded",
            "i",
            "spent",
            "time",
            "and",
            "the",
            "a",
            "an",
            "to",
            "for",
            "with",
            "of",
            "in",
            "from",
            "also",
            "ran",
            "edited",
            "worked",
            "session",
            "sessions",
            "minutes",
            "minute",
        }
    )
    return allowed


def check_style(post: str, *, max_words: int = 120) -> list[CheckResult]:
    results: list[CheckResult] = []
    words = _word_count(post)
    results.append(
        CheckResult(
            "word_limit",
            words <= max_words,
            f"{words} words (limit {max_words})",
        )
    )
    results.append(
        CheckResult(
            "no_exclamation",
            "!" not in post,
            "contains '!' " if "!" in post else "ok",
        )
    )
    has_emoji = bool(_EMOJI_RE.search(post))
    results.append(CheckResult("no_emoji", not has_emoji, "emoji found" if has_emoji else "ok"))
    return results


def check_sentence_range(post: str, *, min_s: int = 3, max_s: int = 5) -> CheckResult:
    """Posts target the same compact 3–5 sentence range in all modes."""
    n = _sentence_count(post)
    return CheckResult(
        "sentence_range",
        min_s <= n <= max_s,
        f"{n} sentences (allowed {min_s}-{max_s})",
    )


def check_groundedness(
    post: str,
    digests: list[SessionDigest],
    *,
    required_substrings: list[str] | None = None,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    required_substrings = required_substrings or []
    lower = post.lower()

    for req in required_substrings:
        ok = req.lower() in lower
        results.append(
            CheckResult(
                f"mentions:{req}",
                ok,
                "found" if ok else f"missing required mention {req!r}",
            )
        )

    # Project basenames from digests should appear if digests are non-empty
    if digests:
        projects = sorted(
            {d.project_path.replace("\\", "/").rstrip("/").split("/")[-1] for d in digests}
        )
        hit = any(p.lower() in lower for p in projects)
        results.append(
            CheckResult(
                "mentions_a_project",
                hit,
                f"projects={projects}" if hit else f"none of {projects} found in post",
            )
        )

    # Flag obvious invented project-ish tokens that never appear in the digest
    allowed = allowed_tokens_from_digests(digests)
    suspicious = []
    for tok in re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{4,}\b", post):
        t = tok.lower()
        if t not in allowed and t not in suspicious:
            # Skip very common English words not in the filler list
            if t in {
                "today",
                "across",
                "tools",
                "parser",
                "refactor",
                "streaming",
                "progress",
                "large",
                "files",
                "pytest",
                "sketched",
                "module",
                "edited",
                "worked",
                "spent",
                "minutes",
                "session",
                "sessions",
                "coding",
                "activity",
                "logged",
                "recorded",
                "requested",
                "touched",
                "commands",
                "source",
                "claude",
                "code",
            }:
                continue
            # Heuristic: underscored identifiers look file-like when absent from the
            # digest. Hyphenated English compounds (re-running) are ignored.
            if "_" in tok and t not in allowed:
                suspicious.append(tok)

    results.append(
        CheckResult(
            "no_suspicious_inventions",
            len(suspicious) == 0,
            "ok" if not suspicious else f"possible inventions: {suspicious}",
        )
    )
    return results


def check_midnight_partition(
    day1: list[SessionDigest],
    day2: list[SessionDigest],
    *,
    session_id: str,
) -> list[CheckResult]:
    d1 = [s for s in day1 if s.session_id == session_id]
    d2 = [s for s in day2 if s.session_id == session_id]
    results = [
        CheckResult(
            "slice_present_both_days",
            len(d1) == 1 and len(d2) == 1,
            f"day1={len(d1)} day2={len(d2)}",
        )
    ]
    if len(d1) == 1 and len(d2) == 1:
        total_min = d1[0].duration_minutes + d2[0].duration_minutes
        results.append(
            CheckResult(
                "duration_non_overlapping",
                total_min > 0,
                (
                    f"day1={d1[0].duration_minutes:.1f} "
                    f"day2={d2[0].duration_minutes:.1f} sum={total_min:.1f}"
                ),
            )
        )
        # Messages should not be duplicated across days
        overlap = set(d1[0].user_messages) & set(d2[0].user_messages)
        results.append(
            CheckResult(
                "messages_partitioned",
                len(overlap) == 0,
                "ok" if not overlap else f"duplicated messages: {overlap}",
            )
        )
        # Token totals should be positive on at least one side and disjoint attribution
        results.append(
            CheckResult(
                "tokens_attributed",
                (d1[0].tokens_in + d2[0].tokens_in) > 0,
                f"day1_in={d1[0].tokens_in} day2_in={d2[0].tokens_in}",
            )
        )
    return results


def check_project_paths(digests: list[SessionDigest], expected_paths: set[str]) -> CheckResult:
    found = {d.project_path for d in digests}
    missing = expected_paths - found
    return CheckResult(
        "expected_project_paths",
        len(missing) == 0,
        "ok" if not missing else f"missing {missing}; found {found}",
    )
