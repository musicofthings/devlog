#!/usr/bin/env python3
"""
Daily dev-log generator entrypoint.

Usage (on your own machine, once Claude Code has real sessions logged):
    python main.py --date 2026-07-22
    python main.py --date today

By default reads from ~/.claude (the real Claude Code log location).
For testing against the bundled synthetic sample data:
    python main.py --date 2026-07-22 --claude-root sample_data/claude_code --sample-mode
"""

from devlog.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
