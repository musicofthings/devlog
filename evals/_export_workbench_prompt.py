from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from devlog.digest import build_raw_digest, slice_for_date
from devlog.sources.claude_code import ClaudeCodeParser
from devlog.summarize import SUMMARY_SYSTEM_PROMPT

root = Path("sample_data/claude_code")
raw = ClaudeCodeParser().iter_sessions(root)
digests = slice_for_date(raw, date(2026, 7, 22), ZoneInfo("UTC"))
full = build_raw_digest(digests)
compact = build_raw_digest(digests, compact=True)
user = f"Digest:\n{compact}\n\nPost:"

out = Path("evals/workbench_prompt.md")
out.write_text(
    "# Anthropic Workbench paste kit (token-efficient)\n\n"
    "Model: `claude-sonnet-4-6` (or closest Sonnet)\n"
    "Temperature: 0–0.3\n"
    f"Max tokens: 120\n\n"
    f"Full digest chars: {len(full)} · Compact digest chars: {len(compact)} "
    f"({100 * len(compact) / max(len(full), 1):.0f}% of full)\n\n"
    "## System\n\n```\n" + SUMMARY_SYSTEM_PROMPT + "\n```\n\n## User\n\n```\n" + user + "\n```\n",
    encoding="utf-8",
)
print(f"Wrote {out.resolve()}")
print(f"full={len(full)} compact={len(compact)} sessions={len(digests)}")
