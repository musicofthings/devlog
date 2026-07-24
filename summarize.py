"""
Turns a day's worth of parsed Claude Code SessionDigest objects into a single
short, readable first-person "build log" post.

Uses the Anthropic API if ANTHROPIC_API_KEY is set; otherwise falls back to a
deterministic template so the pipeline is always testable end-to-end without
a key.
"""

from __future__ import annotations
import os
from claude_code_parser import SessionDigest


def build_raw_digest(sessions: list[SessionDigest]) -> str:
    """Condense sessions into a compact text digest to feed the summarizer.
    Kept factual and structured -- the LLM's job is just to narrate this,
    not invent content."""
    if not sessions:
        return "No Claude Code activity recorded today."

    lines = []
    total_minutes = sum(s.duration_minutes for s in sessions)
    projects = sorted({s.project_path for s in sessions})
    lines.append(f"Total active time: {total_minutes:.0f} minutes across {len(sessions)} session(s).")
    lines.append(f"Projects touched: {', '.join(projects)}")

    for s in sessions:
        lines.append(f"\n[Project: {s.project_path}, {s.duration_minutes:.0f} min]")
        if s.user_messages:
            lines.append("  Tasks requested: " + " | ".join(s.user_messages))
        if s.tool_calls:
            tool_summary = ", ".join(f"{k} x{v}" for k, v in s.tool_calls.items())
            lines.append(f"  Tools used: {tool_summary}")
        if s.files_touched:
            lines.append(f"  Files touched: {', '.join(sorted(s.files_touched))}")
        if s.bash_commands:
            lines.append(f"  Commands run: {', '.join(s.bash_commands)}")

    return "\n".join(lines)


SUMMARY_SYSTEM_PROMPT = """You write short, first-person daily "build log" posts for a \
solo developer, based strictly on factual session data provided to you. \
Style: plain, concrete, no hype, no emojis, no exclamation points. 3-5 sentences. \
Mention specific projects and what changed, not generic praise. \
Never invent details not present in the digest."""


def summarize_with_claude(raw_digest: str, api_key: str | None = None) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=SUMMARY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Today's session digest:\n\n{raw_digest}\n\nWrite the post."}],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def summarize_with_template(sessions: list[SessionDigest]) -> str:
    """Deterministic fallback -- no API key required. Good enough to prove
    the pipeline works end-to-end; the Claude-generated version reads better."""
    if not sessions:
        return "No coding activity logged today."

    projects = sorted({s.project_path.split("/")[-1] for s in sessions})
    total_minutes = sum(s.duration_minutes for s in sessions)
    all_tools = {}
    for s in sessions:
        for k, v in s.tool_calls.items():
            all_tools[k] = all_tools.get(k, 0) + v
    top_tools = sorted(all_tools.items(), key=lambda kv: -kv[1])[:3]

    parts = [f"Today: {total_minutes:.0f} min across {', '.join(projects)}."]
    for s in sessions:
        if s.user_messages:
            parts.append(f"On {s.project_path.split('/')[-1]}: {s.user_messages[0]}")
    if top_tools:
        parts.append("Tools: " + ", ".join(f"{k} ({v}x)" for k, v in top_tools) + ".")
    return " ".join(parts)


def generate_post(sessions: list[SessionDigest]) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    raw_digest = build_raw_digest(sessions)
    if api_key:
        try:
            return summarize_with_claude(raw_digest, api_key=api_key)
        except Exception as e:  # network/auth issues -> don't crash the pipeline
            print(f"[warn] Claude summarization failed ({e}); falling back to template.")
    return summarize_with_template(sessions)
