"""Mirror published posts into a local Obsidian vault."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from datetime import date, datetime
from pathlib import Path

from devlog.config import DevlogConfig, default_config_path, load_config
from devlog.knowledge import build_day_meta, parse_post_meta
from devlog.models import SessionDigest
from devlog.vault_graph import existing_day_notes, load_index, refresh_graph, save_index

# The embed sits *between* two comment markers. Wrapping it inside a single
# %% ... %% comment (the pre-graph format) hides it in Reading/Live Preview.
DEVLOG_REGION_START = "%% devlog:daily:start %%"
DEVLOG_REGION_END = "%% devlog:daily:end %%"
_REGION_RE = re.compile(
    re.escape(DEVLOG_REGION_START) + r".*?" + re.escape(DEVLOG_REGION_END),
    re.DOTALL,
)
_LEGACY_REGION_RE = re.compile(
    r"^%%devlog[ \t]*\n.*?^%%[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_DATE_POST_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
_DAILY_NOTES_JSON = '{"folder": "Daily", "format": "YYYY-MM-DD"}\n'
# Graph view colors for a new vault: project hubs, work-type hubs, day notes.
_GRAPH_JSON = json.dumps(
    {
        "colorGroups": [
            {"query": "tag:#devlog/project-hub", "color": {"a": 1, "rgb": 15105570}},
            {"query": "tag:#devlog/work-hub", "color": {"a": 1, "rgb": 3447003}},
            {"query": "tag:#devlog/week", "color": {"a": 1, "rgb": 10181046}},
            {"query": "path:DevLog", "color": {"a": 1, "rgb": 9807270}},
        ]
    },
    indent=2,
) + "\n"


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
    graph_json = obsidian_dir / "graph.json"
    if not graph_json.exists():
        graph_json.write_text(_GRAPH_JSON, encoding="utf-8")
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


def _region_block(wikilink: str) -> str:
    return f"{DEVLOG_REGION_START}\n{wikilink}\n{DEVLOG_REGION_END}"


def upsert_daily_region(existing: str, day: date, wikilink: str) -> str:
    block = _region_block(wikilink)
    text = existing.replace("\r\n", "\n")
    legacy = _LEGACY_REGION_RE.search(text)
    if legacy is not None and not _REGION_RE.search(text):
        # Migrate in place so the embed keeps its position in the note.
        text = text[: legacy.start()] + block + text[legacy.end() :]
    text = _LEGACY_REGION_RE.sub("", text)
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
    stripped = _LEGACY_REGION_RE.sub("", stripped)
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


def _folder(cfg: DevlogConfig) -> str:
    return (cfg.obsidian_folder or "DevLog").strip().strip("/").strip("\\")


def _folder_root(cfg: DevlogConfig) -> Path:
    root = vault_root(cfg)
    assert root is not None
    folder = _folder(cfg)
    return root / folder if folder else root


def refresh_vault(cfg: DevlogConfig, days: dict[str, dict] | None = None) -> dict:
    """Rebuild day notes, hubs, weeklies, and Home from the vault index."""
    root = vault_root(cfg)
    if root is None:
        return {"status": "disabled"}
    if not root.is_dir():
        return {"status": "vault_missing", "vault": str(root)}
    folder_root = _folder_root(cfg)
    if days is None:
        days = load_index(folder_root)
        # A day note deleted by hand in Obsidian stays deleted.
        present = existing_day_notes(folder_root)
        days = {d: m for d, m in days.items() if d in present}
    try:
        save_index(folder_root, days)
        graph = refresh_graph(root, _folder(cfg), days)
    except OSError as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "refreshed", "days": len(days), **graph}


def _upsert_day(
    day: date,
    post_markdown: str,
    digests: list[SessionDigest] | None,
    days: dict[str, dict],
) -> None:
    if digests is not None:
        meta = build_day_meta(day, digests, post_markdown)
    else:
        meta = parse_post_meta(day, post_markdown)
        previous = days.get(day.isoformat())
        # Re-mirroring (backfill, or a post hand-edited in review mode) keeps
        # the session-derived detail and only takes the new prose.
        if previous and previous.get("origin") == "sessions":
            meta = {**previous, "summary": meta["summary"]}
    days[day.isoformat()] = meta


def try_mirror_post(
    cfg: DevlogConfig,
    day: date,
    post_markdown: str,
    digests: list[SessionDigest] | None = None,
    *,
    refresh: bool = True,
) -> dict:
    """Write the day note + Daily Note embed, then relink the whole graph.

    `digests` (available at publish time) give per-project detail; without
    them, metadata is recovered from the post text.
    """
    root = vault_root(cfg)
    if root is None:
        return {"status": "disabled"}
    if not root.is_dir():
        return {"status": "vault_missing", "vault": str(root)}
    try:
        archive = archive_path(cfg, day)
        daily = daily_path(cfg, day)
        folder_root = _folder_root(cfg)
        days = load_index(folder_root)
        _upsert_day(day, post_markdown, digests, days)
        if refresh:
            present = existing_day_notes(folder_root) | {day.isoformat()}
            days = {d: m for d, m in days.items() if d in present}
            outcome = refresh_vault(cfg, days)
            if outcome["status"] == "error":
                return outcome
        else:
            save_index(folder_root, days)
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
    days = load_index(_folder_root(cfg))
    days.pop(day.isoformat(), None)
    outcome = refresh_vault(cfg, days)
    if outcome["status"] == "error":
        return outcome
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
    digests_by_day: dict[date, list[SessionDigest]] | None = None,
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
        digests = digests_by_day.get(day) if digests_by_day is not None else None
        result = try_mirror_post(
            cfg, day, path.read_text(encoding="utf-8"), digests, refresh=False
        )
        if result["status"] == "written":
            written += 1
        elif result["status"] in {"vault_missing", "error"}:
            return {**result, "count": written, "days": days}
    status = "dry_run" if dry_run else "written"
    if not dry_run and written:
        root = _folder_root(cfg)
        index = load_index(root)
        present = existing_day_notes(root) | set(days)
        refreshed = refresh_vault(cfg, {d: m for d, m in index.items() if d in present})
        if refreshed["status"] == "error":
            return {**refreshed, "count": written, "days": days}
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


def _format_refresh(outcome: dict) -> str:
    if outcome.get("status") != "refreshed":
        return _format_obsidian_outcome(outcome)
    return (
        f"obsidian refreshed: {outcome['days']} day(s), "
        f"{len(outcome['written'])} note(s) updated, {len(outcome['removed'])} removed"
    )


def _rescan(cfg: DevlogConfig) -> dict[date, list[SessionDigest]]:
    """Read every source once and slice it per day (one pass, not one per post)."""
    from devlog.digest import slice_for_date
    from devlog.publish import collect_raw_sessions

    raw = collect_raw_sessions(cfg)
    tz = datetime.now().astimezone().tzinfo
    days = {
        event.timestamp.astimezone(tz).date()
        for session in raw
        for event in session.events
    }
    return {day: slice_for_date(raw, day, tz) for day in days}


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
    parser.add_argument(
        "--rescan",
        action="store_true",
        help="Re-read local session logs for per-project detail (slower, richer links)",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Regenerate hubs, weeklies, and Home from the vault index only",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if not args.backfill and not args.date and not args.reindex:
        parser.error("specify --backfill, --date, and/or --reindex")

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

    if args.reindex and not args.backfill and not args.date:
        if args.dry_run:
            print("obsidian dry_run: would regenerate hubs from the vault index")
            return 0
        outcome = refresh_vault(cfg)
        print(outcome if args.verbose else _format_refresh(outcome))
        return 1 if outcome.get("status") in {"vault_missing", "error"} else 0

    digests_by_day: dict[date, list[SessionDigest]] | None = None
    if args.rescan and not args.dry_run:
        digests_by_day = _rescan(cfg)

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
                cfg,
                target,
                post_path.read_text(encoding="utf-8"),
                digests_by_day.get(target) if digests_by_day is not None else None,
            )
            if outcome["status"] == "written":
                outcome["count"] = 1
                outcome["days"] = [target.isoformat()]
    else:
        outcome = backfill_posts(
            cfg,
            posts_dir,
            target=target,
            dry_run=args.dry_run,
            digests_by_day=digests_by_day,
        )

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
