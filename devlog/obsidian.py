"""Mirror published posts into a local Obsidian vault."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from datetime import date
from pathlib import Path

from devlog.config import DevlogConfig, default_config_path, load_config

DEVLOG_REGION_START = "%%devlog"
DEVLOG_REGION_END = "%%"
_REGION_RE = re.compile(
    r"^%%devlog[ \t]*\n.*?^%%[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_DATE_POST_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
_DAILY_NOTES_JSON = '{"folder": "Daily", "format": "YYYY-MM-DD"}\n'


def obsidian_app_config_path() -> Path:
    """Obsidian's vault registry (`obsidian.json`)."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return root / "obsidian" / "obsidian.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    return Path.home() / ".config" / "obsidian" / "obsidian.json"


def default_new_vault_path() -> Path:
    return Path.home() / "Documents" / "DevLog"


def _read_vault_records(app_config: Path) -> list[dict]:
    try:
        data = json.loads(app_config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    vaults = data.get("vaults")
    if not isinstance(vaults, dict):
        return []
    records: list[dict] = []
    for meta in vaults.values():
        if not isinstance(meta, dict):
            continue
        raw = meta.get("path")
        if not raw or not isinstance(raw, str):
            continue
        records.append(
            {
                "path": Path(raw),
                "ts": int(meta["ts"]) if isinstance(meta.get("ts"), int) else 0,
                "open": bool(meta.get("open")),
            }
        )
    return records


def detect_obsidian_vault(*, app_config: Path | None = None) -> Path | None:
    """Return the preferred existing vault from Obsidian's registry, if any.

    Prefers the currently open vault, then the most recently used path that
    still exists on disk.
    """
    config_path = app_config if app_config is not None else obsidian_app_config_path()
    existing = [r for r in _read_vault_records(config_path) if r["path"].is_dir()]
    if not existing:
        return None
    open_vaults = [r for r in existing if r["open"]]
    chosen = max(open_vaults or existing, key=lambda r: r["ts"])
    return chosen["path"].resolve()


def create_obsidian_vault(path: Path) -> Path:
    """Create a folder Obsidian can open as a vault. Idempotent."""
    path.mkdir(parents=True, exist_ok=True)
    obsidian_dir = path / ".obsidian"
    obsidian_dir.mkdir(exist_ok=True)
    app_json = obsidian_dir / "app.json"
    if not app_json.exists():
        app_json.write_text("{}\n", encoding="utf-8")
    daily_json = obsidian_dir / "daily-notes.json"
    if not daily_json.exists():
        daily_json.write_text(_DAILY_NOTES_JSON, encoding="utf-8")
    (path / "DevLog").mkdir(exist_ok=True)
    (path / "Daily").mkdir(exist_ok=True)
    return path


def register_obsidian_vault(path: Path, *, app_config: Path | None = None) -> None:
    """Add `path` to Obsidian's vault list without changing the open vault."""
    config_path = app_config if app_config is not None else obsidian_app_config_path()
    if not config_path.exists():
        return
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(data, dict):
        return
    vaults = data.get("vaults")
    if not isinstance(vaults, dict):
        vaults = {}
        data["vaults"] = vaults
    resolved = path.resolve()
    for meta in vaults.values():
        if not isinstance(meta, dict):
            continue
        raw = meta.get("path")
        if isinstance(raw, str) and Path(raw).resolve() == resolved:
            return
    vaults[secrets.token_hex(8)] = {"path": str(resolved), "ts": 0}
    config_path.write_text(json.dumps(data), encoding="utf-8")


def ensure_obsidian_vault(
    *,
    app_config: Path | None = None,
    create_path: Path | None = None,
    register: bool = True,
) -> tuple[Path, str]:
    """Detect an existing vault, or create the default DevLog vault.

    Returns (path, source) where source is `open`, `recent`, or `created`.
    """
    config_path = app_config if app_config is not None else obsidian_app_config_path()
    records = [r for r in _read_vault_records(config_path) if r["path"].is_dir()]
    if records:
        open_vaults = [r for r in records if r["open"]]
        chosen = max(open_vaults or records, key=lambda r: r["ts"])
        source = "open" if chosen["open"] else "recent"
        return chosen["path"].resolve(), source
    target = create_path if create_path is not None else default_new_vault_path()
    created = create_obsidian_vault(target)
    if register:
        register_obsidian_vault(created, app_config=config_path)
    return created, "created"


def vault_root(cfg: DevlogConfig) -> Path | None:
    raw = (cfg.obsidian_vault or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def archive_path(cfg: DevlogConfig, day: date) -> Path:
    root = vault_root(cfg)
    if root is None:
        raise ValueError("obsidian_vault is not set")
    folder = (cfg.obsidian_folder or "DevLog").strip().strip("/").strip("\\")
    return root / folder / f"{day.isoformat()}.md"


def daily_path(cfg: DevlogConfig, day: date) -> Path:
    root = vault_root(cfg)
    if root is None:
        raise ValueError("obsidian_vault is not set")
    daily = (cfg.obsidian_daily_folder or "").strip().strip("/").strip("\\")
    if daily:
        return root / daily / f"{day.isoformat()}.md"
    return root / f"{day.isoformat()}.md"


def wikilink_for(cfg: DevlogConfig, day: date) -> str:
    folder = (cfg.obsidian_folder or "DevLog").strip().strip("/").strip("\\")
    target = f"{folder}/{day.isoformat()}" if folder else day.isoformat()
    return f"![[{target}]]"


def render_archive(day: date, post_markdown: str) -> str:
    body = post_markdown.strip() + "\n"
    return (
        "---\n"
        f"date: {day.isoformat()}\n"
        "tags:\n"
        "  - devlog\n"
        "---\n\n"
        f"{body}"
    )


def _region_block(wikilink: str) -> str:
    return f"{DEVLOG_REGION_START}\n{wikilink}\n{DEVLOG_REGION_END}"


def upsert_daily_region(existing: str, day: date, wikilink: str) -> str:
    block = _region_block(wikilink)
    text = existing.replace("\r\n", "\n")
    if not text.strip():
        return f"# {day.isoformat()}\n\n{block}\n"
    matches = list(_REGION_RE.finditer(text))
    if matches:
        for match in reversed(matches[1:]):
            text = text[: match.start()] + text[match.end() :]
        match = _REGION_RE.search(text)
        assert match is not None
        text = text[: match.start()] + block + text[match.end() :]
        return text.rstrip() + "\n"
    return text.rstrip() + f"\n\n{block}\n"


def strip_daily_region(text: str) -> str:
    stripped = _REGION_RE.sub("", text.replace("\r\n", "\n"))
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    return stripped.strip() + "\n"


def planned_paths(cfg: DevlogConfig, day: date) -> dict:
    if vault_root(cfg) is None:
        return {"status": "disabled"}
    archive = archive_path(cfg, day)
    daily = daily_path(cfg, day)
    return {
        "status": "enabled",
        "archive": str(archive),
        "daily": str(daily),
        "wikilink": wikilink_for(cfg, day),
    }


def try_mirror_post(cfg: DevlogConfig, day: date, post_markdown: str) -> dict:
    root = vault_root(cfg)
    if root is None:
        return {"status": "disabled"}
    if not root.is_dir():
        return {"status": "vault_missing", "vault": str(root)}
    try:
        archive = archive_path(cfg, day)
        daily = daily_path(cfg, day)
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(render_archive(day, post_markdown), encoding="utf-8")
        previous = daily.read_text(encoding="utf-8") if daily.exists() else ""
        daily.parent.mkdir(parents=True, exist_ok=True)
        daily.write_text(
            upsert_daily_region(previous, day, wikilink_for(cfg, day)),
            encoding="utf-8",
        )
    except OSError as exc:
        return {"status": "error", "error": str(exc)}
    return {
        "status": "written",
        "archive": str(archive),
        "daily": str(daily),
    }


def remove_mirrored_post(cfg: DevlogConfig, day: date) -> dict:
    root = vault_root(cfg)
    if root is None:
        return {"status": "disabled"}
    if not root.is_dir():
        return {"status": "vault_missing", "vault": str(root)}
    try:
        archive = archive_path(cfg, day)
        daily = daily_path(cfg, day)
        if archive.exists():
            archive.unlink()
        if daily.exists():
            leftover = strip_daily_region(daily.read_text(encoding="utf-8"))
            daily.write_text(leftover, encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "error": str(exc)}
    return {
        "status": "removed",
        "archive": str(archive),
        "daily": str(daily),
    }


def should_remove_from_vault(cfg: DevlogConfig, *, also_obsidian: bool) -> bool:
    if also_obsidian:
        return True
    return cfg.obsidian_on_delete == "remove"


def backfill_posts(
    cfg: DevlogConfig,
    posts_dir: Path,
    *,
    target: date | None = None,
    dry_run: bool = False,
) -> dict:
    if vault_root(cfg) is None:
        return {"status": "disabled", "count": 0, "days": []}
    if target is not None:
        paths = [posts_dir / f"{target.isoformat()}.md"]
    else:
        paths = sorted(posts_dir.glob("*.md"))
    days: list[str] = []
    written = 0
    for path in paths:
        match = _DATE_POST_RE.match(path.name)
        if match is None or not path.is_file():
            continue
        day = date.fromisoformat(match.group(1))
        days.append(day.isoformat())
        if dry_run:
            continue
        result = try_mirror_post(cfg, day, path.read_text(encoding="utf-8"))
        if result["status"] == "written":
            written += 1
        elif result["status"] in {"vault_missing", "error"}:
            return {**result, "count": written, "days": days}
    status = "dry_run" if dry_run else "written"
    return {"status": status, "count": len(days) if dry_run else written, "days": days}


def _format_obsidian_outcome(outcome: dict) -> str:
    status = outcome.get("status")
    if status == "disabled":
        return "obsidian: disabled"
    if status == "vault_missing":
        return f"obsidian: vault missing ({outcome.get('vault')})"
    if status == "error":
        return f"obsidian: error ({outcome.get('error')})"
    if status == "dry_run":
        days = ", ".join(outcome.get("days") or [])
        return f"obsidian dry_run: {outcome.get('count', 0)} post(s)" + (
            f" [{days}]" if days else ""
        )
    archive = outcome.get("archive")
    daily = outcome.get("daily")
    extra = ""
    if archive:
        extra = f" archive={archive}"
    if daily:
        extra += f" daily={daily}"
    count = outcome.get("count")
    if count is not None:
        return f"obsidian {status}: {count} post(s){extra}"
    return f"obsidian {status}:{extra}"


def cmd_obsidian(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mirror published posts into a local Obsidian vault"
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Mirror every posts/*.md into the vault",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="YYYY-MM-DD, 'today', or 'yesterday' (mirror one day)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Config path (default: ~/.config/devlog/config.toml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print intended vault paths; do not write notes",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if not args.backfill and not args.date:
        parser.error("specify --backfill and/or --date")

    cfg_path = args.config or default_config_path()
    try:
        cfg = load_config(cfg_path)
    except (OSError, ValueError) as exc:
        print(f"Could not load config at {cfg_path}: {exc}")
        return 2
    if cfg is None:
        print(f"No config at {cfg_path}. Run: devlog init")
        return 2

    target: date | None = None
    if args.date:
        from devlog.publish import resolve_publish_date

        try:
            target = resolve_publish_date(args.date)
        except ValueError:
            print(f"Invalid --date {args.date!r}")
            return 2

    repo = Path(cfg.repo_path).expanduser()
    posts_dir = repo / "posts"
    if target is not None and not args.backfill:
        post_path = posts_dir / f"{target.isoformat()}.md"
        if not post_path.exists():
            print(f"No post to mirror: {post_path}")
            return 2
        if args.dry_run:
            outcome = planned_paths(cfg, target)
            outcome["days"] = [target.isoformat()]
            outcome["count"] = 1 if outcome.get("status") != "disabled" else 0
            if outcome.get("status") == "enabled":
                outcome["status"] = "dry_run"
        else:
            outcome = try_mirror_post(
                cfg, target, post_path.read_text(encoding="utf-8")
            )
            if outcome["status"] == "written":
                outcome["count"] = 1
                outcome["days"] = [target.isoformat()]
    else:
        outcome = backfill_posts(cfg, posts_dir, target=target, dry_run=args.dry_run)

    if args.verbose:
        print(outcome)
    else:
        print(_format_obsidian_outcome(outcome))
        if args.dry_run and outcome.get("archive"):
            print(f"archive: {outcome['archive']}")
            print(f"daily: {outcome['daily']}")
        elif args.dry_run and outcome.get("days"):
            for day_str in outcome["days"]:
                day = date.fromisoformat(day_str)
                paths = planned_paths(cfg, day)
                if paths.get("status") == "enabled":
                    print(f"{day_str}: {paths['archive']}")
    if outcome.get("status") in {"vault_missing", "error"}:
        return 1
    return 0
