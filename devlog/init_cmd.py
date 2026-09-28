"""Interactive and non-interactive setup for Daily Dev Log."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

from devlog.config import (
    DEFAULT_SOURCES,
    OBSIDIAN_ON_DELETE,
    PUBLIC_DETAIL_LEVELS,
    PUBLISH_MODES,
    DevlogConfig,
    default_config_path,
    load_config,
    save_config,
)
from devlog.literature import zotero_installed
from devlog.obsidian import (
    create_obsidian_vault,
    default_new_vault_path,
    detect_obsidian_vault,
    ensure_obsidian_vault,
    register_obsidian_vault,
)
from devlog.scheduler import (
    cron_line,
    host_platform,
    macos_log_path,
    register_launchd_agent,
    register_windows_task,
    scheduler_name,
    try_enable_task_history,
    unregister_launchd_agent,
    unregister_windows_task,
    write_publish_now_command,
    write_publish_now_shortcut,
)


def _prompt(label: str, default: str) -> str:
    raw = input(f"{label} [{default}]: ").strip()
    return raw or default


NONE_ANSWER = "-"


def _prompt_optional(label: str, default: str, none_means: str) -> str:
    """Like _prompt, but `-` clears the value (Enter alone always takes the default)."""
    raw = _prompt(f"{label} ({NONE_ANSWER} = {none_means})", default)
    return "" if raw == NONE_ANSWER else raw


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


def _root_field(source: str) -> str:
    return "claude_root" if source == "claude_code" else f"{source}_root"


def detected_sources(cfg: DevlogConfig) -> list[str]:
    """Sources whose data folder exists on this machine (all of them if none do)."""
    found = [s for s in DEFAULT_SOURCES if cfg.root_for(s).exists()]
    return found or list(DEFAULT_SOURCES)


def build_config_from_prompts(existing: DevlogConfig | None = None) -> DevlogConfig:
    """Ask for each setting, pre-filled so that Enter keeps it.

    Re-running init pre-fills your current config; a first run pre-fills what
    it detects (sources with data on this machine, the open Obsidian vault).
    Only the data folders of the sources you keep are asked for.
    """
    cfg = copy.deepcopy(existing) if existing else DevlogConfig()
    if existing is None:
        cfg.sources = detected_sources(cfg)
    cfg.sources = _prompt_list("sources (comma-separated)", cfg.sources)
    for source in cfg.sources:
        field = _root_field(source)
        if hasattr(cfg, field):
            setattr(cfg, field, _prompt(field, getattr(cfg, field)))
    cfg.repo_path = _prompt("repo_path (local git clone)", cfg.repo_path).replace("\\", "/")
    cfg.publish_mode = _prompt(f"publish_mode ({'|'.join(PUBLISH_MODES)})", cfg.publish_mode)
    cfg.schedule_time = _prompt("schedule_time (HH:MM local)", cfg.schedule_time)
    cfg.remote = _prompt("remote", cfg.remote)
    cfg.branch = _prompt("branch", cfg.branch)
    cfg.public_detail = _prompt(
        "public_detail: how much of your prompts reaches the public post "
        f"({'|'.join(PUBLIC_DETAIL_LEVELS)}; only verbatim quotes prompts)",
        cfg.public_detail,
    )
    cfg.allow_external_api = _prompt_bool(
        "allow transcript text to be sent to an external API? (yes|no)",
        cfg.allow_external_api,
    )
    if existing is not None:
        vault_default, vault_label = existing.obsidian_vault, "obsidian_vault"
    elif (detected := detect_obsidian_vault()) is not None:
        vault_default, vault_label = str(detected), "obsidian_vault, detected"
    else:
        vault_default = str(default_new_vault_path())
        vault_label = "obsidian_vault, created if missing"
    vault = _prompt_optional(vault_label, vault_default.replace("\\", "/"), "no vault")
    cfg.obsidian_vault = vault.replace("\\", "/")
    if vault:
        vault_path = Path(vault).expanduser()
        if not (vault_path / ".obsidian").is_dir():
            try:
                create_obsidian_vault(vault_path)
                register_obsidian_vault(vault_path)
            except OSError as exc:
                print(f"[warn] Could not prepare Obsidian vault at {vault_path}: {exc}")
        cfg.obsidian_folder = _prompt("obsidian_folder", cfg.obsidian_folder)
        cfg.obsidian_daily_folder = _prompt_optional(
            "obsidian_daily_folder", cfg.obsidian_daily_folder, "vault root"
        )
        cfg.obsidian_on_delete = _prompt(
            f"obsidian_on_delete ({'|'.join(OBSIDIAN_ON_DELETE)})", cfg.obsidian_on_delete
        )
    return cfg


# Settings init never asks about; they're edited in config.toml by hand, so
# re-running init must not reset them to defaults.
UNPROMPTED_FIELDS = (
    "project_aliases", "topics", "redact_patterns", "publish_empty_days", "model_prices",
    "related_backend", "period_retros", "ollama_url", "ollama_embed_model", "ollama_model",
    "post_writer", "redact_presets", "pipeline_dirs",
    "zotero_url", "mcp_write",
)


def carry_over_unprompted(cfg: DevlogConfig, cfg_path: Path) -> list[str]:
    """Copy hand-edited settings from an existing config into `cfg`."""
    try:
        previous = load_config(cfg_path)
    except (OSError, ValueError):
        return []
    if previous is None:
        return []
    kept = []
    for name in UNPROMPTED_FIELDS:
        value = getattr(previous, name)
        if value != getattr(DevlogConfig(), name):
            setattr(cfg, name, value)
            kept.append(name)
    return kept


def pages_checklist() -> str:
    return (
        "\nGitHub Pages checklist:\n"
        "  1. Push this repo to GitHub (if not already).\n"
        "  2. Settings -> Pages -> Build and deployment -> Source: GitHub Actions.\n"
        "  3. After the first docs/ push, confirm https://<user>.github.io/devlog/\n"
        "  4. Ensure git/gh auth works for publish_mode=auto or pr.\n"
        "\nPrivacy note: public_detail decides what posts reveal: 'summary'\n"
        "(minutes + project count), 'projects' (names + work types; default), or\n"
        "'verbatim' (the text of your prompts). With publish_mode=auto posts go\n"
        "public with no review. Add regexes (MRNs, sample IDs) to redact_patterns\n"
        "in the config file; the Obsidian vault always keeps full detail.\n"
        "\nUse `devlog hide --date YYYY-MM-DD` to soft-hide a post from the public\n"
        "feed (markdown stays in posts/). Use `devlog delete` for a real git removal.\n"
        "With publish_mode=review, nightly writes files locally; confirm with\n"
        "`devlog publish --confirm --date YYYY-MM-DD` when ready to push.\n"
        "\nObsidian: `devlog init` detects your current vault from Obsidian's\n"
        "app config, or creates ~/Documents/DevLog (with .obsidian) if none is\n"
        "found. Answer - at the vault prompt to skip it. Each publish mirrors the post into\n"
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
        help="Register the nightly publish job (Windows Task Scheduler or macOS launchd; "
        "on Linux, prints the cron line)",
    )
    sched.add_argument(
        "--no-schedule",
        action="store_true",
        help="Do not schedule, and remove a nightly job registered earlier",
    )
    sched.add_argument(
        "--schedule-only",
        action="store_true",
        help="(Re)register the nightly job from the existing config without asking "
        "anything or changing the config",
    )
    args = parser.parse_args(argv)

    cfg_path = args.config or default_config_path()
    if args.schedule_only:
        existing = load_config(cfg_path)
        if existing is None:
            print(f"No config at {cfg_path}; run `devlog init` first.")
            return 2
        return _schedule(host_platform(), existing, cfg_path)
    if args.defaults:
        cfg = DevlogConfig()
        cfg.sources = detected_sources(cfg)
        try:
            path, source = ensure_obsidian_vault()
            cfg.obsidian_vault = str(path).replace("\\", "/")
            print(f"Obsidian vault ({source}): {cfg.obsidian_vault}")
        except OSError as exc:
            print(f"[warn] Could not set up Obsidian vault: {exc}")
    else:
        try:
            existing = load_config(cfg_path)
        except (OSError, ValueError) as exc:
            print(f"[note] Ignoring the existing config ({exc}); starting from detected values.")
            existing = None
        if existing is None:
            print("Daily Dev Log setup — detected values are pre-filled; press Enter to accept.\n")
        else:
            print(f"Daily Dev Log setup — your current settings ({cfg_path}) are pre-filled;\n"
                  "press Enter to keep each one.\n")
        try:
            cfg = build_config_from_prompts(existing)
        except ValueError as exc:
            print(f"Invalid config: {exc}")
            return 2

    carried = carry_over_unprompted(cfg, cfg_path)
    if carried and args.defaults:
        print("Kept from your existing config: " + ", ".join(carried))
    if cfg.zotero_url == DevlogConfig().zotero_url and not zotero_installed():
        cfg.zotero_url = ""
        print('Zotero not found: citekey lookups are off (set zotero_url to turn them on).')

    try:
        cfg.validate()
    except ValueError as exc:
        print(f"Invalid config: {exc}")
        return 2

    saved = save_config(cfg, cfg_path)
    print(f"Wrote config: {saved}")
    print(pages_checklist())

    platform = host_platform()
    _write_shortcut(platform, cfg, saved)

    do_schedule = args.schedule
    if not args.defaults and not args.schedule and not args.no_schedule:
        if platform == "linux":
            do_schedule = True  # prints the cron line; nothing is registered
        else:
            ans = input(
                f"Publish nightly at {cfg.schedule_time} with {scheduler_name()}? [y/N]: "
            ).strip().lower()
            do_schedule = ans in {"y", "yes"}
    if args.defaults and not args.schedule:
        do_schedule = False

    if do_schedule:
        code = _schedule(platform, cfg, saved)
        if code:
            return code
    elif args.no_schedule:
        _unschedule(platform)

    print(
        "Publish gate is controlled by publish_mode in config "
        f"(current: {cfg.publish_mode!r}). Change anytime by editing {saved}."
    )
    return 0


def _write_shortcut(platform: str, cfg: DevlogConfig, config_path: Path) -> None:
    """A double-clickable publish-now file on the Desktop (Windows .cmd, macOS .command)."""
    if platform == "linux":
        return
    try:
        if platform == "windows":
            shortcut = write_publish_now_shortcut(cfg, config_path=config_path)
        else:
            shortcut = write_publish_now_command(cfg, config_path=config_path)
        print(f"Publish-now shortcut written: {shortcut} (double-click to publish immediately)")
    except Exception as exc:  # noqa: BLE001
        print(f"[note] Could not write publish-now shortcut: {exc}")


def _schedule(platform: str, cfg: DevlogConfig, config_path: Path) -> int:
    if platform == "linux":
        print(
            "\nNightly publish on Linux: add this line with `crontab -e`:\n"
            f"  {cron_line(cfg, config_path=config_path)}"
        )
        return 0
    if platform == "macos":
        try:
            plist = register_launchd_agent(cfg, config_path=config_path)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] Could not register the launchd job: {exc}")
            return 1
        print(
            f"launchd job registered: {plist}\n"
            f"  Runs daily at {cfg.schedule_time} (or at wake, if the Mac was asleep).\n"
            f"  Log: {macos_log_path()}"
        )
        if cfg.allow_external_api and not _plist_has_api_key(plist):
            print(
                "[note] launchd doesn't see ANTHROPIC_API_KEY from your shell, so nightly "
                "posts use the built-in template. To let Claude write them, add\n"
                "  <key>ANTHROPIC_API_KEY</key><string>sk-ant-...</string>\n"
                f"under EnvironmentVariables in {plist}, then reload it with "
                "`devlog init --schedule-only` (which keeps the key)."
            )
        return 0
    try:
        task_name = register_windows_task(cfg, config_path=config_path)
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
    return 0


def _plist_has_api_key(plist: Path) -> bool:
    import plistlib

    try:
        env = plistlib.loads(plist.read_bytes()).get("EnvironmentVariables") or {}
    except (OSError, ValueError):
        return False
    return bool(env.get("ANTHROPIC_API_KEY"))


def _unschedule(platform: str) -> None:
    try:
        if platform == "windows":
            unregister_windows_task()
        elif platform == "macos":
            unregister_launchd_agent()
    except Exception:  # noqa: BLE001
        pass
