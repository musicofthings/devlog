"""Interactive and non-interactive setup for Daily Dev Log."""

from __future__ import annotations

import argparse
from pathlib import Path

from devlog.config import (
    DEFAULT_OBSIDIAN_DAILY_FOLDER,
    DEFAULT_OBSIDIAN_FOLDER,
    DEFAULT_OBSIDIAN_ON_DELETE,
    DEFAULT_PUBLISH_MODE,
    DEFAULT_SOURCES,
    OBSIDIAN_ON_DELETE,
    PUBLISH_MODES,
    DevlogConfig,
    default_config_path,
    default_opencode_root,
    default_repo_path,
    default_warp_root,
    save_config,
)
from devlog.obsidian import (
    create_obsidian_vault,
    default_new_vault_path,
    detect_obsidian_vault,
    ensure_obsidian_vault,
    register_obsidian_vault,
)
from devlog.scheduler import (
    register_windows_task,
    try_enable_task_history,
    unregister_windows_task,
    write_publish_now_shortcut,
)


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
    grok_root = _prompt("grok_root", "~/.grok")
    copilot_root = _prompt("copilot_root", "~/.copilot")
    opencode_root = _prompt("opencode_root", default_opencode_root())
    warp_root = _prompt("warp_root", default_warp_root())
    vitreous_root = _prompt("vitreous_root", "~/.vitreous")
    antigravity_root = _prompt("antigravity_root", "~/.gemini")
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
    detected = detect_obsidian_vault()
    if detected is not None:
        vault_default = str(detected).replace("\\", "/")
        vault_label = "obsidian_vault (detected; blank to skip)"
    else:
        vault_default = str(default_new_vault_path()).replace("\\", "/")
        vault_label = "obsidian_vault (will create if missing; blank to skip)"
    obsidian_vault = _prompt(vault_label, vault_default)
    obsidian_folder = DEFAULT_OBSIDIAN_FOLDER
    obsidian_daily_folder = DEFAULT_OBSIDIAN_DAILY_FOLDER
    obsidian_on_delete = DEFAULT_OBSIDIAN_ON_DELETE
    if obsidian_vault:
        vault_path = Path(obsidian_vault)
        if not (vault_path / ".obsidian").is_dir():
            try:
                create_obsidian_vault(vault_path)
                register_obsidian_vault(vault_path)
            except OSError as exc:
                print(f"[warn] Could not prepare Obsidian vault at {vault_path}: {exc}")
        obsidian_folder = _prompt("obsidian_folder", DEFAULT_OBSIDIAN_FOLDER)
        obsidian_daily_folder = _prompt(
            "obsidian_daily_folder (blank = vault root)",
            DEFAULT_OBSIDIAN_DAILY_FOLDER,
        )
        obsidian_on_delete = _prompt(
            f"obsidian_on_delete ({'|'.join(OBSIDIAN_ON_DELETE)})",
            DEFAULT_OBSIDIAN_ON_DELETE,
        )
    return DevlogConfig(
        sources=sources,
        claude_root=claude_root,
        codex_root=codex_root,
        cursor_root=cursor_root,
        grok_root=grok_root,
        copilot_root=copilot_root,
        opencode_root=opencode_root,
        warp_root=warp_root,
        vitreous_root=vitreous_root,
        antigravity_root=antigravity_root,
        repo_path=repo_path.replace("\\", "/"),
        publish_mode=publish_mode,
        schedule_time=schedule_time,
        remote=remote,
        branch=branch,
        allow_external_api=allow_external_api,
        obsidian_vault=obsidian_vault.replace("\\", "/"),
        obsidian_folder=obsidian_folder,
        obsidian_daily_folder=obsidian_daily_folder,
        obsidian_on_delete=obsidian_on_delete,
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
        "\nUse `devlog hide --date YYYY-MM-DD` to soft-hide a post from the public\n"
        "feed (markdown stays in posts/). Use `devlog delete` for a real git removal.\n"
        "With publish_mode=review, nightly writes files locally; confirm with\n"
        "`devlog publish --confirm --date YYYY-MM-DD` when ready to push.\n"
        "\nObsidian: `devlog init` detects your current vault from Obsidian's\n"
        "app config, or creates ~/Documents/DevLog (with .obsidian) if none is\n"
        "found. Blank the vault path to skip. Each publish mirrors the post into\n"
        "DevLog/YYYY-MM-DD.md and a Daily Note embed. Hard delete never removes\n"
        "vault notes unless obsidian_on_delete=remove or you pass\n"
        "`devlog delete --also-obsidian`.\n"
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
        try:
            path, source = ensure_obsidian_vault()
            cfg.obsidian_vault = str(path).replace("\\", "/")
            print(f"Obsidian vault ({source}): {cfg.obsidian_vault}")
        except OSError as exc:
            print(f"[warn] Could not set up Obsidian vault: {exc}")
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

    try:
        shortcut = write_publish_now_shortcut(cfg, config_path=saved)
        print(f"Publish-now shortcut written: {shortcut} (double-click to publish immediately)")
    except Exception as exc:  # noqa: BLE001
        print(f"[note] Could not write publish-now shortcut: {exc}")

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
        if try_enable_task_history():
            print("Task Scheduler history logging enabled.")
        else:
            print(
                "[note] Could not enable Task Scheduler history logging (needs an "
                "elevated/Administrator PowerShell -- opening a regular PowerShell "
                "window is not enough). Without it, if this task ever silently "
                "stops running, there will be no log explaining why. To enable it "
                "later, open PowerShell as Administrator and run:\n"
                '  wevtutil sl "Microsoft-Windows-TaskScheduler/Operational" /e:true'
            )
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
