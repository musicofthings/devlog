"""
Turns a day's worth of parsed SessionDigest objects into a single short,
readable first-person "build log" post.

Uses the Anthropic API if ANTHROPIC_API_KEY is set; otherwise falls back to a
deterministic template so the pipeline is always testable end-to-end without
a key.
"""

from __future__ import annotations

import os
import re

from devlog.digest import basename, build_raw_digest
from devlog.models import SessionDigest

# Kept tight: every word here is billed as input on every call.
SUMMARY_SYSTEM_PROMPT = (
    "Write a first-person daily build-log from the digest only. "
    "Exactly 3 or 4 short sentences. No hype, emojis, or exclamation points. "
    "Name projects and concrete changes. Invent nothing."
)

# Cap output for ~3-4 sentences with headroom so posts never truncate mid-sentence.
CLAUDE_MAX_TOKENS = 200
CLAUDE_MODEL = "claude-sonnet-5"
MAX_POST_SENTENCES = 5

# Sentence boundary: terminal punctuation followed by whitespace (or end of
# string). Keeps decimals like "1.5 hours" intact.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _clamp_sentences(text: str, max_sentences: int = MAX_POST_SENTENCES) -> str:
    """Hard-cap sentence count to keep posts (and evals) token-efficient."""
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text.strip()) if p.strip()]
    parts = parts[:max_sentences]
    if parts and not parts[-1].endswith((".", "!", "?")):
        parts[-1] += "."
    return " ".join(parts)


def summarize_with_claude(
    raw_digest: str,
    api_key: str | None = None,
    model: str = CLAUDE_MODEL,
) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=CLAUDE_MAX_TOKENS,
        # Sonnet 5 thinks by default and thinking counts against max_tokens;
        # this task is trivial, so keep the budget for the post itself.
        thinking={"type": "disabled"},
        system=SUMMARY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Digest:\n{raw_digest}\n\nPost:"}],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return _clamp_sentences(text)


def summarize_with_template(sessions: list[SessionDigest]) -> str:
    """Deterministic fallback -- no API key required. Good enough to prove
    the pipeline works end-to-end; the Claude-generated version reads better."""
    if not sessions:
        return "No coding activity logged today."

    projects = sorted({basename(s.project_path) for s in sessions})
    total_minutes = sum(s.duration_minutes for s in sessions)
    all_tools = {}
    for s in sessions:
        for k, v in s.tool_calls.items():
            all_tools[k] = all_tools.get(k, 0) + v
    top_tools = sorted(all_tools.items(), key=lambda kv: -kv[1])[:3]

    parts = [f"Today: {total_minutes:.0f} min across {', '.join(projects)}."]
    for s in sessions:
        if s.user_messages:
            parts.append(f"On {basename(s.project_path)}: {s.user_messages[0]}")
    if top_tools:
        parts.append("Tools: " + ", ".join(f"{k} ({v}x)" for k, v in top_tools) + ".")
    return " ".join(parts)


def generate_post(sessions: list[SessionDigest], model: str | None = None) -> str:
    # Empty day: never spend tokens on the API.
    if not sessions:
        return summarize_with_template(sessions)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    # Compact digest for the LLM path; full digest remains available for audits.
    raw_digest = build_raw_digest(sessions, compact=True)
    if api_key:
        try:
            return summarize_with_claude(raw_digest, api_key=api_key, model=model or CLAUDE_MODEL)
        except Exception as e:  # network/auth issues -> don't crash the pipeline
            print(f"[warn] Claude summarization failed ({e}); falling back to template.")
    return summarize_with_template(sessions)
