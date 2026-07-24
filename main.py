#!/usr/bin/env python3
"""
Daily dev-log generator, phase 1: Claude Code only.

Usage (on your own machine, once Claude Code has real sessions logged):
    python main.py --date 2026-07-22
    python main.py --date today

By default reads from ~/.claude (the real Claude Code log location).
For testing against the bundled synthetic sample data:
    python main.py --date 2026-07-22 --claude-root sample_data --sample-mode
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from claude_code_parser import find_sessions_for_date, parse_session_file
from summarize import generate_post


def find_sessions_sample_mode(sample_root: Path, target_date: str):
    """The bundled sample_data/ folder mimics the *contents* of ~/.claude/projects/
    directly (no extra 'projects' nesting), so we scan it the same way
    find_sessions_for_date scans claude_root/projects."""
    sessions = []
    for project_folder in sample_root.iterdir():
        if not project_folder.is_dir():
            continue
        for session_file in project_folder.glob("*.jsonl"):
            digest = parse_session_file(session_file)
            if digest and (
                digest.start_time.strftime("%Y-%m-%d") == target_date
                or digest.end_time.strftime("%Y-%m-%d") == target_date
            ):
                sessions.append(digest)
    return sorted(sessions, key=lambda s: s.start_time)


def main():
    parser = argparse.ArgumentParser(description="Generate a daily Claude Code build-log post")
    parser.add_argument("--date", default="today", help="YYYY-MM-DD or 'today' (UTC)")
    parser.add_argument("--claude-root", default="~/.claude", help="Root of Claude Code's local data dir")
    parser.add_argument("--sample-mode", action="store_true", help="Treat --claude-root as the sample_data layout")
    args = parser.parse_args()

    target_date = (
        datetime.now(timezone.utc).strftime("%Y-%m-%d") if args.date == "today" else args.date
    )

    if args.sample_mode:
        sessions = find_sessions_sample_mode(Path(args.claude_root), target_date)
    else:
        sessions = find_sessions_for_date(Path(args.claude_root).expanduser(), target_date)

    print(f"=== Found {len(sessions)} session(s) for {target_date} ===\n")
    post = generate_post(sessions)
    print("=== Daily post ===\n")
    print(post)
    print()

    out_path = Path(f"devlog-{target_date}.md")
    out_path.write_text(f"# {target_date}\n\n{post}\n")
    print(f"(saved to {out_path})")


if __name__ == "__main__":
    main()
