"""Soft-hide / unhide a published day without deleting its markdown."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from devlog.config import DevlogConfig, default_config_path, load_config
from devlog.gitutil import GitPublishError, GitRunner, commit_and_push, default_git, git_paths
from devlog.hidden import hide_date, is_hidden, load_hidden_dates, unhide_date
from devlog.site import rebuild_site
from devlog.status import record_event


def _restore_failed_visibility(
    repo: Path,
    hidden_file: Path,
    previous_hidden: str | None,
    artifacts: list[Path],
    *,
    committed: bool,
    git_run: GitRunner,
    branch: str,
) -> str:
    """Undo a failed hide/unhide, mirroring delete/publish recovery."""
    if committed:
        reset = git_run(["git", "reset", "--hard", "HEAD~1"], repo)
        if reset.returncode != 0:
            detail = (reset.stderr or reset.stdout or "git reset failed").strip()
            return (
                "visibility commit is local but unpushed; run "
                f"`git reset --hard HEAD~1` to restore (auto-reset failed: {detail})"
            )
        return "local visibility commit was reset; tree restored"

    if previous_hidden is None:
        hidden_file.unlink(missing_ok=True)
    else:
        hidden_file.write_text(previous_hidden, encoding="utf-8")

    try:
        others = [p for p in artifacts if p != hidden_file]
        paths = git_paths(repo, others) if others else []
        if paths:
            git_run(["git", "checkout", "HEAD", "--", *paths], repo)
    except RuntimeError:
        pass
    rebuild_site(repo, git_run=git_run, branch=branch)
    return "visibility change was rolled back in the working tree"


def _set_visibility(
    cfg: DevlogConfig,
    target: date,
    *,
    hide: bool,
    dry_run: bool = False,
    git_run: GitRunner = default_git,
) -> dict:
    repo = Path(cfg.repo_path).expanduser()
    post_path = repo / "posts" / f"{target.isoformat()}.md"
    action = "hide" if hide else "unhide"

    if not post_path.exists():
        raise FileNotFoundError(f"No post to {action}: {post_path}")

    currently_hidden = is_hidden(repo, target)
    if hide and currently_hidden:
        return {"status": "already_hidden", "date": target.isoformat()}
    if not hide and not currently_hidden:
        return {"status": "not_hidden", "date": target.isoformat()}

    if dry_run:
        return {
            "status": "dry_run",
            "action": action,
            "date": target.isoformat(),
            "post_path": str(post_path),
            "hidden_dates": sorted(load_hidden_dates(repo)),
        }

    if not repo.is_dir():
        raise RuntimeError(f"Configured repository does not exist: {repo}")
    if not (repo / ".git").exists():
        raise RuntimeError(f"Configured repository is not a git checkout: {repo}")

    from devlog.hidden import hidden_path

    hidden_file = hidden_path(repo)
    previous_hidden = (
        hidden_file.read_text(encoding="utf-8") if hidden_file.exists() else None
    )

    if hide:
        hidden_file = hide_date(repo, target)
        event = "hidden"
        message = f"hide: devlog {target.isoformat()}"
    else:
        hidden_file = unhide_date(repo, target)
        event = "unhidden"
        message = f"unhide: devlog {target.isoformat()}"

    status_file = record_event(repo, event=event, date=target.isoformat())
    written = rebuild_site(repo, git_run=git_run, branch=cfg.branch)
    artifacts = [hidden_file, status_file, *written]
    # unhide may have deleted the sidecar; still stage the deletion path.
    if not hide and not hidden_file.exists():
        artifacts.append(hidden_file)

    try:
        commit_and_push(
            repo,
            message,
            artifacts,
            remote=cfg.remote,
            branch=cfg.branch,
            git_run=git_run,
            require_changes=True,
        )
    except RuntimeError as exc:
        committed = isinstance(exc, GitPublishError) and exc.committed
        note = _restore_failed_visibility(
            repo,
            hidden_file,
            previous_hidden,
            artifacts,
            committed=committed,
            git_run=git_run,
            branch=cfg.branch,
        )
        raise RuntimeError(f"{exc} ({note})") from exc

    return {
        "status": "hidden" if hide else "unhidden",
        "date": target.isoformat(),
        "post_path": str(post_path),
    }


def hide_day(
    cfg: DevlogConfig,
    target: date,
    *,
    dry_run: bool = False,
    git_run: GitRunner = default_git,
) -> dict:
    return _set_visibility(cfg, target, hide=True, dry_run=dry_run, git_run=git_run)


def unhide_day(
    cfg: DevlogConfig,
    target: date,
    *,
    dry_run: bool = False,
    git_run: GitRunner = default_git,
) -> dict:
    return _set_visibility(cfg, target, hide=False, dry_run=dry_run, git_run=git_run)


def _cmd_visibility(argv: list[str] | None, *, hide: bool) -> int:
    action = "hide" if hide else "unhide"
    parser = argparse.ArgumentParser(
        description=(
            f"{'Hide' if hide else 'Unhide'} a published daily post from the public "
            "feed (markdown stays in posts/)"
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
        help="Print what would change; do not touch files or git",
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
        outcome = (
            hide_day(cfg, target, dry_run=args.dry_run)
            if hide
            else unhide_day(cfg, target, dry_run=args.dry_run)
        )
    except FileNotFoundError as exc:
        print(str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"{action.capitalize()} failed: {exc}")
        return 1

    if args.verbose:
        print(outcome)
    else:
        print(f"{outcome.get('status')}: {outcome.get('date')}")
    return 0


def cmd_hide(argv: list[str] | None = None) -> int:
    return _cmd_visibility(argv, hide=True)


def cmd_unhide(argv: list[str] | None = None) -> int:
    return _cmd_visibility(argv, hide=False)
