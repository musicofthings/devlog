"""Interactive and non-interactive setup for Daily Dev Log."""

from __future__ import annotations

import argparse
from pathlib import Path

from devlog.config import (
    DEFAULT_PUBLISH_MODE,
    DEFAULT_SOURCES,
    PUBLISH_MODES,
    DevlogConfig,
    default_config_path,
    default_repo_path,
    save_config,
)
from devlog.scheduler import register_windows_task, unregister_windows_task


def _prompt(label: str, default: str) -> str:
    raw = input(f"{label} [{default}]: ").strip()
    return raw or default


def _prompt_list(label: str, default: list[str]) -> list[str]:
    joined = ",".join(default)
    raw = _prompt(label, joined)
    return [p.strip() for p in raw.split(",") if p.strip()]


def _prompt_bool(label: str, default: bool = False) -> bool:
    default_text = "yes" if default else "no"
    raw = _prompt(label, default_text).lower()
    if raw not in {"y", "yes", "true", "n", "no", "false"}:
        raise ValueError(f"{label} must be yes or no")
    return raw in {"y", "yes", "true"}


def build_config_from_prompts() -> DevlogConfig:
    repo = str(default_repo_path()).replace("\\", "/")
    sources = _prompt_list("sources (comma-separated)", list(DEFAULT_SOURCES))
    claude_root = _prompt("claude_root", "~/.claude")
    codex_root = _prompt("codex_root", "~/.codex")
    cursor_root = _prompt("cursor_root", "~/.cursor")
    repo_path = _prompt("repo_path (local git clone)", repo)
    publish_mode = _prompt(
        f"publish_mode ({'|'.join(PUBLISH_MODES)})",
        DEFAULT_PUBLISH_MODE,
    )
    schedule_time = _prompt("schedule_time (HH:MM local)", "06:30")
    remote = _prompt("remote", "origin")
    branch = _prompt("branch", "main")
    allow_external_api = _prompt_bool(
        "allow transcript text to be sent to an external API? (yes|no)", False
    )
    return DevlogConfig(
        sources=sources,
        claude_root=claude_root,
        codex_root=codex_root,
        cursor_root=cursor_root,
        repo_path=repo_path.replace("\\", "/"),
        publish_mode=publish_mode,
        schedule_time=schedule_time,
        remote=remote,
        branch=branch,
        allow_external_api=allow_external_api,
    )


def pages_checklist() -> str:
    return (
        "\nGitHub Pages checklist:\n"
        "  1. Push this repo to GitHub (if not already).\n"
        "  2. Settings -> Pages -> Build and deployment -> Source: GitHub Actions.\n"
        "  3. After the first docs/ push, confirm https://<user>.github.io/devlog/\n"
        "  4. Ensure git/gh auth works for publish_mode=auto or pr.\n"
        "\nPrivacy note: published posts can include project paths AND the text of\n"
        "your prompts to the AI tools. With publish_mode=auto they go public with\n"
        "no review — keep 'manual' or 'pr' unless you accept that.\n"
        "\nUse `devlog delete --date YYYY-MM-DD` to retract a post -- it's a real\n"
        "git removal, recoverable only via git history, not a soft-hide.\n"
    )


def cmd_init(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize Daily Dev Log config")
    parser.add_argument(
        "--defaults",
        action="store_true",
        help="Write default config without prompts",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Config path (default: ~/.config/devlog/config.toml)",
    )
    sched = parser.add_mutually_exclusive_group()
    sched.add_argument(
        "--schedule",
        action="store_true",
        help="Register a Windows scheduled task for nightly publish",
    )
    sched.add_argument(
        "--no-schedule",
        action="store_true",
        help="Do not register a scheduled task",
    )
    args = parser.parse_args(argv)

    cfg_path = args.config or default_config_path()
    if args.defaults:
        cfg = DevlogConfig()
    else:
        print("Daily Dev Log setup — press Enter to accept defaults.\n")
        try:
            cfg = build_config_from_prompts()
        except ValueError as exc:
            print(f"Invalid config: {exc}")
            return 2

    try:
        cfg.validate()
    except ValueError as exc:
        print(f"Invalid config: {exc}")
        return 2

    saved = save_config(cfg, cfg_path)
    print(f"Wrote config: {saved}")
    print(pages_checklist())

    do_schedule = args.schedule
    if not args.defaults and not args.schedule and not args.no_schedule:
        ans = input("Register Windows Task Scheduler job? [y/N]: ").strip().lower()
        do_schedule = ans in {"y", "yes"}
    if args.defaults and not args.schedule:
        do_schedule = False

    if do_schedule:
        try:
            task_name = register_windows_task(cfg, config_path=saved)
            print(f"Scheduled task registered: {task_name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] Could not register scheduled task: {exc}")
            return 1
    elif args.no_schedule:
        try:
            unregister_windows_task()
        except Exception:  # noqa: BLE001
            pass

    print(
        "Publish gate is controlled by publish_mode in config "
        f"(current: {cfg.publish_mode!r}). Change anytime by editing {saved}."
    )
    return 0
