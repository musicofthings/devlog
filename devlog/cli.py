"""Argparse entrypoint with subcommands: run (default), init, publish."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from devlog.config import load_config
from devlog.digest import slice_for_date
from devlog.models import RawSession
from devlog.summarize import generate_post

DEFAULT_SOURCES = "claude_code,codex,cursor"


def build_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a daily build-log post from AI coding session transcripts"
    )
    parser.add_argument("--date", default="today", help="YYYY-MM-DD or 'today' (local timezone)")
    parser.add_argument(
        "--sources",
        default=None,
        help=f"Comma-separated source names (default: config or {DEFAULT_SOURCES})",
    )
    parser.add_argument("--claude-root", default=None, help="Claude Code data root")
    parser.add_argument("--codex-root", default=None, help="Codex data root")
    parser.add_argument("--cursor-root", default=None, help="Cursor data root")
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


def _resolve_roots_and_sources(args: argparse.Namespace) -> tuple[list[str], dict[str, str]]:
    cfg = load_config()
    if args.sources:
        source_names = [s.strip() for s in args.sources.split(",") if s.strip()]
    elif cfg:
        source_names = list(cfg.sources)
    else:
        source_names = [s.strip() for s in DEFAULT_SOURCES.split(",") if s.strip()]

    roots = {
        "claude_code": args.claude_root
        or (cfg.claude_root if cfg else "~/.claude"),
        "codex": args.codex_root or (cfg.codex_root if cfg else "~/.codex"),
        "cursor": args.cursor_root or (cfg.cursor_root if cfg else "~/.cursor"),
    }
    return source_names, roots


def cmd_run(argv: list[str] | None = None) -> int:
    args = build_run_parser().parse_args(argv)

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

    import devlog.sources  # noqa: F401
    from devlog.sources.base import get_sources

    source_names, roots = _resolve_roots_and_sources(args)
    try:
        sources = get_sources(source_names)
    except KeyError as exc:
        print(exc.args[0] if exc.args else str(exc))
        return 2

    raw_sessions: list[RawSession] = []
    for source in sources:
        root = Path(roots.get(source.name, "~/.claude")).expanduser()
        if not root.exists():
            if args.verbose:
                print(f"[{source.name}] no data root at {root} — skipping")
            continue
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
    out_path.write_text(f"# {target_date}\n\n{post}\n", encoding="utf-8")
    print(f"(saved to {out_path})")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "init":
        from devlog.init_cmd import cmd_init

        return cmd_init(argv[1:])
    if argv and argv[0] == "publish":
        from devlog.publish import cmd_publish

        return cmd_publish(argv[1:])
    if argv and argv[0] in {"run", "generate"}:
        return cmd_run(argv[1:])
    return cmd_run(argv)
