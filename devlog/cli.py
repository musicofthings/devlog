"""Argparse entrypoint: collects sessions from registered sources, slices
them to a single local-timezone calendar day, generates a post, and prints
it (writing `devlog-YYYY-MM-DD.md` unless --dry-run)."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from devlog.digest import slice_for_date
from devlog.models import RawSession
from devlog.summarize import generate_post


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a daily build-log post from AI coding session transcripts"
    )
    parser.add_argument("--date", default="today", help="YYYY-MM-DD or 'today' (local timezone)")
    parser.add_argument(
        "--sources",
        default="claude_code",
        help="Comma-separated list of source names to collect from (default: claude_code)",
    )
    parser.add_argument(
        "--claude-root",
        default="~/.claude",
        help="Root of Claude Code's local data dir",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the post but do not write a file"
    )
    parser.add_argument("--verbose", action="store_true", help="Print extra diagnostic information")
    parser.add_argument(
        "--sample-mode",
        action="store_true",
        help="Legacy flag; sample layout is auto-detected (kept for compatibility)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    now = datetime.now().astimezone()
    tz = now.tzinfo
    if args.date == "today":
        target_date = now.date()
    else:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Invalid --date {args.date!r}: expected YYYY-MM-DD or 'today'")
            return 2

    # Import for side-effect registration of built-in source parsers.
    import devlog.sources  # noqa: F401
    from devlog.sources.base import get_sources

    source_names = [s.strip() for s in args.sources.split(",") if s.strip()]
    try:
        sources = get_sources(source_names)
    except KeyError as exc:
        print(exc.args[0] if exc.args else str(exc))
        return 2

    # All current sources read from --claude-root. ClaudeCodeParser auto-detects
    # sample vs real layout (projects/ subdir or not); --sample-mode is legacy.
    root = Path(args.claude_root).expanduser()

    raw_sessions: list[RawSession] = []
    if not root.exists():
        print(f"No data root found at {root} — nothing to report.")
    else:
        for source in sources:
            found = source.iter_sessions(root)
            if args.verbose:
                print(f"[{source.name}] found {len(found)} session(s) under {root}")
            raw_sessions.extend(found)

    digests = slice_for_date(raw_sessions, target_date, tz)

    print(f"=== Found {len(digests)} session(s) for {target_date} ===\n")
    post = generate_post(digests)
    print("=== Daily post ===\n")
    print(post)
    print()

    if args.dry_run:
        return 0

    out_path = Path(f"devlog-{target_date}.md")
    out_path.write_text(f"# {target_date}\n\n{post}\n")
    print(f"(saved to {out_path})")

    return 0
