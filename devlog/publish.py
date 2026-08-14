"""Publish a day's post into the repo and optionally git push / open a PR."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from devlog.config import DevlogConfig, default_config_path, load_config
from devlog.digest import slice_for_date
from devlog.gitutil import GitPublishError, GitRunner, add_and_commit, commit_and_push, git_paths
from devlog.gitutil import default_git as _default_git
from devlog.models import RawSession
from devlog.obsidian import planned_paths, try_mirror_post
from devlog.site import list_posts, rebuild_site, write_post_markdown
from devlog.status import record_event, status_path
from devlog.summarize import generate_post

MANAGED_PATHS = (
    "posts/",
    "docs/log/",
    "docs/.nojekyll",
    "docs/index.html",
    ".devlog-status.json",
    ".devlog-hidden.json",
)


def _restore_failed_publish(
    repo: Path,
    post_path: Path,
    previous_body: str | None,
    status_file: Path,
    previous_status: str | None,
    artifacts: list[Path],
    preexisting: set[Path],
    *,
    committed: bool,
    git_run: GitRunner,
    branch: str,
) -> str:
    """Undo a failed auto-publish. Returns a short note for the error message.

    Mirrors delete_cmd recovery: reset an unpushed local commit, or scrub
    written artifacts so the next nightly run is not wedged by dirty managed
    paths / an existing post that would skip republish.
    """
    if committed:
        reset = git_run(["git", "reset", "--hard", "HEAD~1"], repo)
        if reset.returncode != 0:
            detail = (reset.stderr or reset.stdout or "git reset failed").strip()
            return (
                "publish commit is local but unpushed; run "
                f"`git reset --hard HEAD~1` to restore (auto-reset failed: {detail})"
            )
        # Confirm/review and force-overwrite keep a local body in memory; if the
        # reset dropped an unpushed new file, put that body back so the operator
        # can retry without regenerating.
        if previous_body is not None and not post_path.exists():
            post_path.parent.mkdir(parents=True, exist_ok=True)
            post_path.write_text(previous_body, encoding="utf-8")
        return "local publish commit was reset; tree restored for retry"

    if previous_body is None:
        post_path.unlink(missing_ok=True)
    else:
        post_path.parent.mkdir(parents=True, exist_ok=True)
        post_path.write_text(previous_body, encoding="utf-8")

    if previous_status is None:
        status_file.unlink(missing_ok=True)
    else:
        status_file.write_text(previous_status, encoding="utf-8")

    preexisting_resolved = {p.resolve() for p in preexisting}
    restored_paths: list[Path] = []
    for artifact in artifacts:
        if artifact in {post_path, status_file}:
            continue
        if artifact.resolve() in preexisting_resolved:
            restored_paths.append(artifact)
        else:
            artifact.unlink(missing_ok=True)

    try:
        paths = git_paths(repo, restored_paths) if restored_paths else []
        if paths:
            git_run(["git", "checkout", "HEAD", "--", *paths], repo)
    except RuntimeError:
        pass

    # Sync generated HTML with whatever posts remain (including a restored body).
    if list_posts(repo / "posts"):
        rebuild_site(repo, git_run=git_run, branch=branch)
    return "publish artifacts were rolled back; tree clean for retry"


def resolve_publish_date(raw: str, *, today: date | None = None) -> date:
    today = today or datetime.now().astimezone().date()
    if raw in {"yesterday", ""}:
        return today - timedelta(days=1)
    if raw == "today":
        return today
    return date.fromisoformat(raw)


def collect_digests(cfg: DevlogConfig, target: date):
    import devlog.sources  # noqa: F401
    from devlog.sources.base import get_sources

    sources = get_sources(list(cfg.sources))
    raw: list[RawSession] = []
    for source in sources:
        root = cfg.root_for(source.name)
        if not root.exists():
            continue
        raw.extend(source.iter_sessions(root))
    tz = datetime.now().astimezone().tzinfo
    return slice_for_date(raw, target, tz)


def _ensure_managed_paths_clean(repo: Path, git_run: GitRunner) -> None:
    """Refuse to sweep pre-existing drafts or edits into an automated commit."""
    status = git_run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", *MANAGED_PATHS],
        repo,
    )
    if status.returncode != 0:
        raise RuntimeError(status.stderr or status.stdout or "git status failed")
    if status.stdout.strip():
        raise RuntimeError(
            "Managed publish paths already contain uncommitted changes; "
            "review or commit them before publishing:\n" + status.stdout.strip()
        )


def _git_publish_auto(
    repo: Path,
    day: date,
    artifacts: list[Path],
    *,
    remote: str,
    branch: str,
    git_run: GitRunner,
) -> None:
    commit_and_push(
        repo,
        f"publish: devlog {day.isoformat()}",
        artifacts,
        remote=remote,
        branch=branch,
        git_run=git_run,
    )


def _git_publish_pr(
    repo: Path,
    day: date,
    artifacts: list[Path],
    *,
    remote: str,
    base_branch: str,
    git_run: GitRunner,
    gh_run: GitRunner | None = None,
) -> None:
    gh_run = gh_run or _default_git
    branch = f"devlog/post-{day.isoformat()}"
    current = git_run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo)
    if current.returncode != 0:
        raise RuntimeError(current.stderr or current.stdout or "git rev-parse failed")
    original_branch = current.stdout.strip() or base_branch
    # Branch off the base branch explicitly. Branching off HEAD would stack
    # each day's PR on the previous (possibly unmerged) devlog branch.
    checkout = git_run(["git", "checkout", "-B", branch, base_branch], repo)
    if checkout.returncode != 0:
        raise RuntimeError(checkout.stderr or checkout.stdout or "git checkout failed")

    error: Exception | None = None
    try:
        add_and_commit(
            repo, f"publish: devlog {day.isoformat()}", artifacts, git_run, require_changes=True
        )

        push = git_run(["git", "push", "-u", remote, branch], repo)
        if push.returncode != 0:
            raise RuntimeError(push.stderr or push.stdout or "git push failed")

        pr = gh_run(
            [
                "gh",
                "pr",
                "create",
                "--base",
                base_branch,
                "--head",
                branch,
                "--title",
                f"devlog {day.isoformat()}",
                "--body",
                f"Automated daily build log for {day.isoformat()}.",
            ],
            repo,
        )
        if pr.returncode != 0:
            # PR may already exist; treat duplicate as soft success if message says so.
            err = (pr.stderr or pr.stdout or "").lower()
            if "already exists" not in err:
                raise RuntimeError(pr.stderr or pr.stdout or "gh pr create failed")
    except Exception as exc:  # noqa: BLE001
        error = exc
    finally:
        back = git_run(["git", "checkout", original_branch], repo)
        if back.returncode != 0:
            restore_error = RuntimeError(
                back.stderr or back.stdout or "git checkout original branch failed"
            )
            raise restore_error from error

    if error is not None:
        raise error


def publish_day(
    cfg: DevlogConfig,
    target: date,
    *,
    force: bool = False,
    dry_run: bool = False,
    git_run: GitRunner = _default_git,
    gh_run: GitRunner | None = None,
) -> dict:
    repo = Path(cfg.repo_path).expanduser()
    posts_dir = repo / "posts"
    post_path = posts_dir / f"{target.isoformat()}.md"

    if post_path.exists() and not force:
        return {
            "status": "skipped",
            "reason": f"{post_path} already exists (use --force to overwrite)",
            "post_path": str(post_path),
        }

    if not dry_run:
        if not repo.is_dir():
            raise RuntimeError(f"Configured repository does not exist: {repo}")
        if cfg.publish_mode in {"auto", "pr"}:
            if not (repo / ".git").exists():
                raise RuntimeError(f"Configured repository is not a git checkout: {repo}")
            _ensure_managed_paths_clean(repo, git_run)

    digests = collect_digests(cfg, target)
    body = generate_post(
        digests,
        model=cfg.model,
        allow_external_api=cfg.allow_external_api,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "date": target.isoformat(),
            "post": body,
            "sessions": len(digests),
            "publish_mode": cfg.publish_mode,
            "obsidian": planned_paths(cfg, target),
        }

    previous_body = post_path.read_text(encoding="utf-8") if post_path.exists() else None
    status_file_path = status_path(repo)
    previous_status = (
        status_file_path.read_text(encoding="utf-8") if status_file_path.exists() else None
    )
    preexisting: set[Path] = set()
    if post_path.exists():
        preexisting.add(post_path)
    if status_file_path.exists():
        preexisting.add(status_file_path)
    log_dir = repo / "docs" / "log"
    if log_dir.exists():
        preexisting.update(log_dir.glob("*"))
    for extra in (repo / "docs" / ".nojekyll", repo / "docs" / "index.html"):
        if extra.exists():
            preexisting.add(extra)

    write_post_markdown(posts_dir, target, body, force=force)
    status_file = record_event(repo, event="published", date=target.isoformat())
    written = rebuild_site(repo, git_run=git_run, branch=cfg.branch)
    artifacts = [post_path, status_file, *written]
    post_markdown = post_path.read_text(encoding="utf-8")
    obsidian_result = try_mirror_post(cfg, target, post_markdown)

    result = {
        "status": "written",
        "date": target.isoformat(),
        "post_path": str(post_path),
        "site_files": [str(p) for p in written],
        "publish_mode": cfg.publish_mode,
        "sessions": len(digests),
        "obsidian": obsidian_result,
    }

    if cfg.publish_mode == "manual":
        result["next_steps"] = (
            f"Review {post_path}, then commit posts/ and docs/log/ and push to GitHub."
        )
        return result

    if cfg.publish_mode == "review":
        result["status"] = "pending_review"
        result["next_steps"] = (
            f"Review {post_path}, then run: "
            f"devlog publish --confirm --date {target.isoformat()}"
        )
        return result

    if cfg.publish_mode == "auto":
        try:
            _git_publish_auto(
                repo,
                target,
                artifacts,
                remote=cfg.remote,
                branch=cfg.branch,
                git_run=git_run,
            )
        except RuntimeError as exc:
            committed = isinstance(exc, GitPublishError) and exc.committed
            note = _restore_failed_publish(
                repo,
                post_path,
                previous_body,
                status_file,
                previous_status,
                artifacts,
                preexisting,
                committed=committed,
                git_run=git_run,
                branch=cfg.branch,
            )
            raise RuntimeError(f"{exc} ({note})") from exc
        result["status"] = "published_auto"
        return result

    if cfg.publish_mode == "pr":
        _git_publish_pr(
            repo,
            target,
            artifacts,
            remote=cfg.remote,
            base_branch=cfg.branch,
            git_run=git_run,
            gh_run=gh_run,
        )
        result["status"] = "published_pr"
        return result

    raise ValueError(f"Unknown publish_mode: {cfg.publish_mode!r}")


def confirm_publish_day(
    cfg: DevlogConfig,
    target: date,
    *,
    dry_run: bool = False,
    git_run: GitRunner = _default_git,
) -> dict:
    """Push an already-written post (review gate) without regenerating."""
    repo = Path(cfg.repo_path).expanduser()
    post_path = repo / "posts" / f"{target.isoformat()}.md"
    if not post_path.exists():
        raise FileNotFoundError(
            f"No post to confirm: {post_path}. Run publish without --confirm first."
        )

    if dry_run:
        return {
            "status": "dry_run",
            "date": target.isoformat(),
            "post_path": str(post_path),
            "action": "confirm_push",
        }

    if not repo.is_dir():
        raise RuntimeError(f"Configured repository does not exist: {repo}")
    if not (repo / ".git").exists():
        raise RuntimeError(f"Configured repository is not a git checkout: {repo}")
    # Review-mode writes leave managed paths dirty on purpose; confirm pushes them.

    status_file_path = status_path(repo)
    previous_status = (
        status_file_path.read_text(encoding="utf-8") if status_file_path.exists() else None
    )
    preexisting: set[Path] = {post_path}
    if status_file_path.exists():
        preexisting.add(status_file_path)
    log_dir = repo / "docs" / "log"
    if log_dir.exists():
        preexisting.update(log_dir.glob("*"))
    for extra in (repo / "docs" / ".nojekyll", repo / "docs" / "index.html"):
        if extra.exists():
            preexisting.add(extra)

    previous_body = post_path.read_text(encoding="utf-8")
    status_file = record_event(repo, event="published", date=target.isoformat())
    written = rebuild_site(repo, git_run=git_run, branch=cfg.branch)
    artifacts = [post_path, status_file, *written]

    try:
        _git_publish_auto(
            repo,
            target,
            artifacts,
            remote=cfg.remote,
            branch=cfg.branch,
            git_run=git_run,
        )
    except RuntimeError as exc:
        committed = isinstance(exc, GitPublishError) and exc.committed
        note = _restore_failed_publish(
            repo,
            post_path,
            previous_body,
            status_file,
            previous_status,
            artifacts,
            preexisting,
            committed=committed,
            git_run=git_run,
            branch=cfg.branch,
        )
        raise RuntimeError(f"{exc} ({note})") from exc

    return {
        "status": "published_confirmed",
        "date": target.isoformat(),
        "post_path": str(post_path),
        "publish_mode": cfg.publish_mode,
    }


def cmd_publish(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish a daily build-log post")
    parser.add_argument(
        "--date",
        default="yesterday",
        help="YYYY-MM-DD, 'today', or 'yesterday' (default: yesterday)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Config path (default: ~/.config/devlog/config.toml)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing post")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Push an already-written post (review gate) without regenerating",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and print; do not write files or run git",
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
        target = resolve_publish_date(args.date)
    except ValueError:
        print(f"Invalid --date {args.date!r}")
        return 2

    try:
        if args.confirm:
            outcome = confirm_publish_day(cfg, target, dry_run=args.dry_run)
        else:
            outcome = publish_day(
                cfg,
                target,
                force=args.force,
                dry_run=args.dry_run,
            )
    except FileNotFoundError as exc:
        print(str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Publish failed: {exc}")
        return 1

    if args.dry_run and not args.confirm:
        if args.verbose:
            details = {key: value for key, value in outcome.items() if key != "post"}
            print(details)
        obsidian = outcome.get("obsidian") or {}
        if obsidian.get("status") == "enabled":
            print(f"obsidian archive: {obsidian.get('archive')}")
            print(f"obsidian daily: {obsidian.get('daily')}")
        print(outcome.get("post", ""))
    elif args.verbose:
        print(outcome)
    else:
        print(f"{outcome.get('status')}: {outcome.get('date', target.isoformat())}")
        if outcome.get("next_steps"):
            print(outcome["next_steps"])
        obsidian = outcome.get("obsidian") or {}
        status = obsidian.get("status")
        if status == "vault_missing":
            print(f"[warn] Obsidian vault missing; skipped mirror: {obsidian.get('vault')}")
        elif status == "error":
            print(f"[warn] Obsidian mirror failed: {obsidian.get('error')}")
        elif status == "written":
            print(f"obsidian: {obsidian.get('archive')}")
    return 0
