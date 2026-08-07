"""Delete a previously published daily post and push the removal."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from devlog.config import DevlogConfig, default_config_path, load_config
from devlog.gitutil import GitRunner, commit_and_push, default_git
from devlog.site import rebuild_site
from devlog.status import record_event


def delete_day(
    cfg: DevlogConfig,
    target: date,
    *,
    dry_run: bool = False,
    git_run: GitRunner = default_git,
) -> dict:
    repo = Path(cfg.repo_path).expanduser()
    post_path = repo / "posts" / f"{target.isoformat()}.md"

    if not post_path.exists():
        raise FileNotFoundError(f"No post to delete: {post_path}")

    if dry_run:
        return {
            "status": "dry_run",
            "date": target.isoformat(),
            "post_path": str(post_path),
        }

    if not repo.is_dir():
        raise RuntimeError(f"Configured repository does not exist: {repo}")
    if not (repo / ".git").exists():
        raise RuntimeError(f"Configured repository is not a git checkout: {repo}")

    post_path.unlink()
    status_file = record_event(repo, event="deleted", date=target.isoformat())
    written = rebuild_site(repo, git_run=git_run, branch=cfg.branch)
    artifacts = [post_path, status_file, *written]

    try:
        commit_and_push(
            repo,
            f"delete: devlog {target.isoformat()}",
            artifacts,
            remote=cfg.remote,
            branch=cfg.branch,
            git_run=git_run,
            require_changes=True,
        )
    except RuntimeError as exc:
        relative = post_path.relative_to(repo).as_posix()
        raise RuntimeError(
            f"{exc} (the post file at {post_path} was already removed from the "
            f"working tree; run `git checkout -- {relative}` to restore it if needed)"
        ) from exc

    return {
        "status": "deleted",
        "date": target.isoformat(),
        "post_path": str(post_path),
    }


def cmd_delete(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Delete a previously published daily post (real git removal — "
            "recoverable only via git history, not a soft-hide)"
        )
    )
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Config path (default: ~/.config/devlog/config.toml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be removed; do not touch files or git",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    cfg_path = args.config or default_config_path()
    try:
        cfg = load_config(cfg_path)
    except (OSError, ValueError) as exc:
        print(f"Could not load config at {cfg_path}: {exc}")
        return 2
    if cfg is None:
        print(f"No config at {cfg_path}. Run: devlog init")
        return 2

    try:
        target = date.fromisoformat(args.date)
    except ValueError:
        print(f"Invalid --date {args.date!r}: expected YYYY-MM-DD")
        return 2

    try:
        outcome = delete_day(cfg, target, dry_run=args.dry_run)
    except FileNotFoundError as exc:
        print(str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Delete failed: {exc}")
        return 1

    if args.verbose:
        print(outcome)
    else:
        print(f"{outcome.get('status')}: {outcome.get('date')}")
    return 0
