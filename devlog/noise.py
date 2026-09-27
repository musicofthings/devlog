"""Filter harness-injected text and low-signal prompts out of user messages.

Agent CLIs inject system context into the transcript as if the user typed it
(MCP tool manifests, AGENTS.md bootstraps, skill preambles). Left in, it shows
up in posts as "I worked on devlog: <mcp_meta_tools> You have access to ...".
"""

from __future__ import annotations

import re

# A message that opens with an XML-ish tag is harness context, not a prompt
# (<mcp_meta_tools>, <system-reminder>, <environment_context>, <INSTRUCTIONS>).
_LEADING_TAG_RE = re.compile(r"^\s*<[A-Za-z][\w:-]*(\s[^>]*)?>")
_INJECTED_PREFIXES = (
    "# agents.md instructions",
    "# claude.md",
    "base directory for this skill:",
    "caveat: the messages below were generated",
    "this session is being continued from a previous conversation",
    "[request interrupted",
    "<command-name>",
    "<local-command-stdout>",
)
_LOW_SIGNAL_RE = re.compile(
    r"^\s*(?:please\s+)?(?:"
    r"try again|continue(?: from where you left off)?|go on|go ahead|proceed|"
    r"resume(?: session| the session)?|save(?: the)? session|yes|yep|y|no|ok(?:ay)?|"
    r"sure|thanks?(?: you)?|done|next|retry|keep going|lgtm"
    r")[\s.!?]*$",
    re.IGNORECASE,
)


def is_injected_prompt(text: str) -> bool:
    """True when `text` is tool/harness context rather than something the user asked."""
    stripped = text.lstrip()
    if not stripped:
        return True
    if _LEADING_TAG_RE.match(stripped):
        return True
    lowered = stripped.lower()
    return any(lowered.startswith(prefix) for prefix in _INJECTED_PREFIXES)


def is_low_signal_prompt(text: str) -> bool:
    """True for continuation nudges ("try again", "resume session") with no topic."""
    return bool(_LOW_SIGNAL_RE.match(text))


def headline_task(messages: list[str]) -> str | None:
    """The most descriptive prompt: first non-low-signal one, else the first."""
    for message in messages:
        if not is_low_signal_prompt(message):
            return message
    return messages[0] if messages else None
