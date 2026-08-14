"""Delete a previously published daily post and push the removal."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from devlog.config import DevlogConfig, default_config_path, load_config
from devlog.gitutil import GitPublishError, GitRunner, commit_and_push, default_git, git_paths
from devlog.obsidian import remove_mirrored_post, should_remove_from_vault
from devlog.site import rebuild_site
from devlog.status import record_event


def _restore_failed_delete(
    repo: Path,
    post_path: Path,
    body: str,
    artifacts: list[Path],
    *,
    committed: bool,
    git_run: GitRunner,
    branch: str,
) -> str:
    """Undo a failed delete. Returns a short note for the error message."""
    if committed:
        reset = git_run(["git", "reset", "--hard", "HEAD~1"], repo)
        if reset.returncode != 0:
            detail = (reset.stderr or reset.stdout or "git reset failed").strip()
            return (
                "delete commit is local but unpushed; run "
                f"`git reset --hard HEAD~1` to restore (auto-reset failed: {detail})"
            )
        return "local delete commit was reset; post restored"

    post_path.write_text(body, encoding="utf-8")
    try:
        others = [p for p in artifacts if p != post_path]
        paths = git_paths(repo, others) if others else []
        if paths:
            git_run(["git", "checkout", "HEAD", "--", *paths], repo)
    except RuntimeError:
        pass
    rebuild_site(repo, git_run=git_run, branch=branch)
    return "post file and site were restored in the working tree"


def delete_day(
    cfg: DevlogConfig,
    target: date,
    *,
    dry_run: bool = False,
    git_run: GitRunner = default_git,
    also_obsidian: bool = False,
) -> dict:
    repo = Path(cfg.repo_path).expanduser()
    post_path = repo / "posts" / f"{target.isoformat()}.md"

    if not post_path.exists():
        raise FileNotFoundError(f"No post to delete: {post_path}")

    if dry_run:
        obsidian_status = (
            "remove" if should_remove_from_vault(cfg, also_obsidian=also_obsidian) else "preserve"
        )
        return {
            "status": "dry_run",
            "date": target.isoformat(),
            "post_path": str(post_path),
            "obsidian": {"status": obsidian_status},
        }

    if not repo.is_dir():
        raise RuntimeError(f"Configured repository does not exist: {repo}")
    if not (repo / ".git").exists():
        raise RuntimeError(f"Configured repository is not a git checkout: {repo}")

    body = post_path.read_text(encoding="utf-8")
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
        committed = isinstance(exc, GitPublishError) and exc.committed
        note = _restore_failed_delete(
            repo,
            post_path,
            body,
            artifacts,
            committed=committed,
            git_run=git_run,
            branch=cfg.branch,
        )
        raise RuntimeError(f"{exc} ({note})") from exc

    if should_remove_from_vault(cfg, also_obsidian=also_obsidian):
        obsidian_result = remove_mirrored_post(cfg, target)
    else:
        obsidian_result = {"status": "preserved"}

    return {
        "status": "deleted",
        "date": target.isoformat(),
        "post_path": str(post_path),
        "obsidian": obsidian_result,
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
    parser.add_argument(
        "--also-obsidian",
        action="store_true",
        help="Also remove the mirrored Obsidian archive note and Daily Note embed",
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
        outcome = delete_day(
            cfg, target, dry_run=args.dry_run, also_obsidian=args.also_obsidian
        )
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
        obsidian = outcome.get("obsidian") or {}
        status = obsidian.get("status")
        if status == "removed":
            print(f"obsidian: removed {obsidian.get('archive', '')}".rstrip())
        elif status == "error":
            print(f"[warn] Obsidian vault note was not removed: {obsidian.get('error')}")
        elif status == "vault_missing":
            print(f"[warn] Obsidian vault missing; left notes untouched: {obsidian.get('vault')}")
    return 0
