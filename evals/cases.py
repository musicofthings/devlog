"""Eval case runners against sample_data fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from devlog.digest import slice_for_date
from devlog.sources.claude_code import ClaudeCodeParser
from devlog.sources.codex import CodexParser
from devlog.sources.cursor import CursorParser
from devlog.summarize import generate_post, summarize_with_template
from evals.rubric import (
    EvalResult,
    check_groundedness,
    check_midnight_partition,
    check_project_paths,
    check_sentence_range,
    check_style,
)

SAMPLE_ROOT = Path(__file__).resolve().parents[1] / "sample_data" / "claude_code"
CODEX_ROOT = Path(__file__).resolve().parents[1] / "sample_data" / "codex"
CURSOR_ROOT = Path(__file__).resolve().parents[1] / "sample_data" / "cursor"
# Fixture timestamps are UTC; use a fixed TZ so local-machine TZ doesn't flap results.
EVAL_TZ = ZoneInfo("UTC")


@dataclass
class CaseSpec:
    case_id: str
    description: str


CASES: list[CaseSpec] = [
    CaseSpec("e2e_2026_07_22_template", "Multi-project day -> template post grounded + styled"),
    CaseSpec("cwd_path_2026_07_23", "variant-caller cwd wins over dash-ambiguous folder name"),
    CaseSpec("midnight_partition", "span session sliced across 07-22 / 07-23 without double-count"),
    CaseSpec("malformed_tolerated", "malformed JSONL session still parses usable lines"),
    CaseSpec("empty_day", "day with no activity -> empty template, no crash"),
    CaseSpec("codex_2026_07_20", "Codex rollout cwd + tools grounded in template post"),
    CaseSpec("cursor_2026_07_20", "Cursor agent-transcript path decode + tools"),
    CaseSpec("multi_source_2026_07_20", "Codex+Cursor same day merge into one post"),
]


def _load_raw():
    return ClaudeCodeParser().iter_sessions(SAMPLE_ROOT)


def run_e2e_2026_07_22_template(*, live: bool = False) -> EvalResult:
    result = EvalResult("e2e_2026_07_22_template")
    digests = slice_for_date(_load_raw(), date(2026, 7, 22), EVAL_TZ)
    result.add("has_sessions", len(digests) >= 2, f"n={len(digests)}")

    if live:
        post = generate_post(digests, allow_external_api=True)
        result.add(
            "live_generate_ok", bool(post.strip()), "empty post" if not post.strip() else "ok"
        )
        for c in check_style(post, max_words=120):
            result.checks.append(c)
        result.checks.append(check_sentence_range(post, min_s=3, max_s=5))
        for c in check_groundedness(post, digests):
            result.checks.append(c)
        projects = sorted(
            {d.project_path.replace("\\", "/").rstrip("/").split("/")[-1].lower() for d in digests}
        )
        hits = sum(1 for p in projects if p in post.lower())
        result.add(
            "covers_multiple_projects",
            hits >= 2,
            f"hit {hits}/{len(projects)} basenames {projects}",
        )
    else:
        post = summarize_with_template(digests)
        for c in check_style(post, max_words=120):
            result.checks.append(c)
        result.checks.append(check_sentence_range(post, min_s=3, max_s=5))
        for c in check_groundedness(
            post,
            digests,
            required_substrings=["variantgpt"],
        ):
            result.checks.append(c)
    return result


def run_cwd_path_2026_07_23() -> EvalResult:
    result = EvalResult("cwd_path_2026_07_23")
    digests = slice_for_date(_load_raw(), date(2026, 7, 23), EVAL_TZ)
    result.checks.append(
        check_project_paths(digests, {"/Users/dev/code/variant-caller", "/Users/dev/code/span"})
    )
    bad = any(d.project_path == "/Users/dev/code/variant/caller" for d in digests)
    result.add("no_lossy_variant_caller", not bad, "lossy path present" if bad else "ok")
    post = summarize_with_template(digests)
    for c in check_groundedness(post, digests, required_substrings=["variant-caller"]):
        result.checks.append(c)
    for c in check_style(post):
        result.checks.append(c)
    return result


def run_midnight_partition() -> EvalResult:
    result = EvalResult("midnight_partition")
    raw = _load_raw()
    day1 = slice_for_date(raw, date(2026, 7, 22), EVAL_TZ)
    day2 = slice_for_date(raw, date(2026, 7, 23), EVAL_TZ)
    for c in check_midnight_partition(day1, day2, session_id="session-midnight"):
        result.checks.append(c)
    return result


def run_malformed_tolerated() -> EvalResult:
    result = EvalResult("malformed_tolerated")
    raw = _load_raw()
    bad = [s for s in raw if s.session_id == "session-malformed"]
    result.add("malformed_session_parsed", len(bad) == 1, f"n={len(bad)}")
    if bad:
        has_msg = any(e.user_message for e in bad[0].events)
        result.add(
            "has_user_message",
            has_msg,
            "ok" if has_msg else "no user message recovered",
        )
    digests = slice_for_date(raw, date(2026, 7, 22), EVAL_TZ)
    result.add("day_still_produces_digests", len(digests) >= 1, f"n={len(digests)}")
    return result


def run_empty_day() -> EvalResult:
    result = EvalResult("empty_day")
    digests = slice_for_date(_load_raw(), date(2099, 1, 1), EVAL_TZ)
    result.add("no_sessions", digests == [], f"n={len(digests)}")
    post = summarize_with_template(digests)
    result.add("empty_template", post == "No coding activity logged today.", repr(post))
    for c in check_style(post):
        result.checks.append(c)
    return result


def run_codex_2026_07_20() -> EvalResult:
    result = EvalResult("codex_2026_07_20")
    raw = CodexParser().iter_sessions(CODEX_ROOT)
    digests = slice_for_date(raw, date(2026, 7, 20), EVAL_TZ)
    result.add("has_codex_session", len(digests) >= 1, f"n={len(digests)}")
    result.add(
        "source_is_codex",
        all(d.source == "codex" for d in digests),
        ",".join(sorted({d.source for d in digests})) or "none",
    )
    paths = {d.project_path.replace("\\", "/") for d in digests}
    result.add(
        "cwd_gurukul",
        any("gurukul" in p.lower() for p in paths),
        str(paths),
    )
    post = summarize_with_template(digests)
    for c in check_groundedness(post, digests, required_substrings=["gurukul"]):
        result.checks.append(c)
    for c in check_style(post):
        result.checks.append(c)
    return result


def run_cursor_2026_07_20() -> EvalResult:
    result = EvalResult("cursor_2026_07_20")
    raw = CursorParser().iter_sessions(CURSOR_ROOT)
    digests = slice_for_date(raw, date(2026, 7, 20), EVAL_TZ)
    result.add("has_cursor_session", len(digests) >= 1, f"n={len(digests)}")
    result.add(
        "decoded_devlog_path",
        any(d.project_path.replace("\\", "/") == "C:/Users/dev/code/devlog" for d in digests),
        str([d.project_path for d in digests]),
    )
    post = summarize_with_template(digests)
    for c in check_groundedness(post, digests, required_substrings=["devlog"]):
        result.checks.append(c)
    for c in check_style(post):
        result.checks.append(c)
    return result


def run_multi_source_2026_07_20() -> EvalResult:
    result = EvalResult("multi_source_2026_07_20")
    raw = CodexParser().iter_sessions(CODEX_ROOT) + CursorParser().iter_sessions(CURSOR_ROOT)
    digests = slice_for_date(raw, date(2026, 7, 20), EVAL_TZ)
    sources = {d.source for d in digests}
    result.add("both_sources", sources == {"codex", "cursor"}, f"sources={sources}")
    result.add("two_plus_sessions", len(digests) >= 2, f"n={len(digests)}")
    post = summarize_with_template(digests)
    result.add("single_post", bool(post.strip()), "empty" if not post.strip() else "ok")
    for c in check_groundedness(post, digests, required_substrings=["gurukul", "devlog"]):
        result.checks.append(c)
    for c in check_style(post):
        result.checks.append(c)
    return result


RUNNERS = {
    "e2e_2026_07_22_template": lambda live=False: run_e2e_2026_07_22_template(live=live),
    "cwd_path_2026_07_23": lambda live=False: run_cwd_path_2026_07_23(),
    "midnight_partition": lambda live=False: run_midnight_partition(),
    "malformed_tolerated": lambda live=False: run_malformed_tolerated(),
    "empty_day": lambda live=False: run_empty_day(),
    "codex_2026_07_20": lambda live=False: run_codex_2026_07_20(),
    "cursor_2026_07_20": lambda live=False: run_cursor_2026_07_20(),
    "multi_source_2026_07_20": lambda live=False: run_multi_source_2026_07_20(),
}


def run_all(*, live: bool = False) -> list[EvalResult]:
    results: list[EvalResult] = []
    for spec in CASES:
        runner = RUNNERS[spec.case_id]
        if spec.case_id == "e2e_2026_07_22_template":
            results.append(runner(live=live))
        else:
            results.append(runner())
    return results
