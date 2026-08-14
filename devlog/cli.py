"""Argparse entrypoint with subcommands: run (default), init, publish."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from devlog.config import DEFAULT_SOURCES, DevlogConfig, load_config
from devlog.digest import slice_for_date
from devlog.models import RawSession
from devlog.summarize import generate_post


def build_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a daily build-log post from AI coding session transcripts"
    )
    parser.add_argument("--date", default="today", help="YYYY-MM-DD or 'today' (local timezone)")
    parser.add_argument(
        "--sources",
        default=None,
        help=f"Comma-separated source names (default: config or {','.join(DEFAULT_SOURCES)})",
    )
    parser.add_argument("--claude-root", default=None, help="Claude Code data root")
    parser.add_argument("--codex-root", default=None, help="Codex data root")
    parser.add_argument("--cursor-root", default=None, help="Cursor data root")
    parser.add_argument("--grok-root", default=None, help="Grok CLI data root")
    parser.add_argument("--copilot-root", default=None, help="GitHub Copilot CLI data root")
    parser.add_argument("--opencode-root", default=None, help="OpenCode data root (opencode.db)")
    parser.add_argument("--warp-root", default=None, help="Warp data root")
    parser.add_argument("--vitreous-root", default=None, help="Vitreous sessions root")
    parser.add_argument("--antigravity-root", default=None, help="Antigravity / Gemini data root")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the post but do not write a file"
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing generated post"
    )
    parser.add_argument(
        "--allow-external-api",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow transcript-derived text to be sent to the configured model API",
    )
    parser.add_argument("--verbose", action="store_true", help="Print extra diagnostic information")
    parser.add_argument(
        "--sample-mode",
        action="store_true",
        help="Legacy flag; sample layout is auto-detected (kept for compatibility)",
    )
    return parser


def _resolve_config(args: argparse.Namespace) -> DevlogConfig:
    """Config file (if any) with CLI overrides applied; defaults otherwise."""
    cfg = load_config() or DevlogConfig()
    if args.sources:
        cfg.sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    for attr in (
        "claude_root",
        "codex_root",
        "cursor_root",
        "grok_root",
        "copilot_root",
        "opencode_root",
        "warp_root",
        "vitreous_root",
        "antigravity_root",
    ):
        val = getattr(args, attr, None)
        if val:
            setattr(cfg, attr, val)
    if args.allow_external_api is not None:
        cfg.allow_external_api = args.allow_external_api
    return cfg


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

    out_path = Path(f"devlog-{target_date}.md")
    if not args.dry_run and out_path.exists() and not args.force:
        print(f"Refusing to overwrite existing post: {out_path} (use --force to replace it)")
        return 1

    import devlog.sources  # noqa: F401
    from devlog.sources.base import get_sources

    try:
        cfg = _resolve_config(args)
    except (OSError, ValueError) as exc:
        print(f"Could not load config: {exc}")
        return 2
    try:
        sources = get_sources(list(cfg.sources))
    except KeyError as exc:
        print(exc.args[0] if exc.args else str(exc))
        return 2

    raw_sessions: list[RawSession] = []
    for source in sources:
        root = cfg.root_for(source.name)
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
    post = generate_post(
        digests,
        model=cfg.model,
        allow_external_api=cfg.allow_external_api,
    )
    print("=== Daily post ===\n")
    print(post)
    print()

    if args.dry_run:
        return 0

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
    if argv and argv[0] == "delete":
        from devlog.delete_cmd import cmd_delete

        return cmd_delete(argv[1:])
    if argv and argv[0] == "hide":
        from devlog.hide_cmd import cmd_hide

        return cmd_hide(argv[1:])
    if argv and argv[0] == "unhide":
        from devlog.hide_cmd import cmd_unhide

        return cmd_unhide(argv[1:])
    if argv and argv[0] == "obsidian":
        from devlog.obsidian import cmd_obsidian

        return cmd_obsidian(argv[1:])
    if argv and argv[0] in {"run", "generate"}:
        return cmd_run(argv[1:])
    return cmd_run(argv)
