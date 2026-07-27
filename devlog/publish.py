"""Publish a day's post into the repo and optionally git push / open a PR."""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from devlog.config import DevlogConfig, default_config_path, load_config
from devlog.digest import slice_for_date
from devlog.models import RawSession
from devlog.site import rebuild_site, write_post_markdown
from devlog.summarize import generate_post

GitRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


def _default_git(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


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


def _git_publish_auto(
    repo: Path,
    day: date,
    *,
    remote: str,
    branch: str,
    git_run: GitRunner,
) -> None:
    paths = ["posts/", "docs/log/", "docs/.nojekyll", "docs/index.html"]
    add = git_run(["git", "add", "--", *paths], repo)
    if add.returncode != 0:
        raise RuntimeError(add.stderr or add.stdout or "git add failed")

    status = git_run(["git", "status", "--porcelain", "--", *paths], repo)
    if not status.stdout.strip():
        return  # nothing new

    msg = f"publish: devlog {day.isoformat()}"
    commit = git_run(["git", "commit", "-m", msg], repo)
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr or commit.stdout or "git commit failed")

    # Rebase onto the remote first so a scheduled push doesn't fail forever
    # after the remote moved (e.g. an edit made on GitHub or another machine).
    pull = git_run(["git", "pull", "--rebase", remote, branch], repo)
    if pull.returncode != 0:
        raise RuntimeError(pull.stderr or pull.stdout or "git pull --rebase failed")

    push = git_run(["git", "push", remote, branch], repo)
    if push.returncode != 0:
        raise RuntimeError(push.stderr or push.stdout or "git push failed")


def _git_publish_pr(
    repo: Path,
    day: date,
    *,
    remote: str,
    base_branch: str,
    git_run: GitRunner,
    gh_run: GitRunner | None = None,
) -> None:
    gh_run = gh_run or _default_git
    branch = f"devlog/post-{day.isoformat()}"
    # Branch off the base branch explicitly. Branching off HEAD would stack
    # each day's PR on the previous (possibly unmerged) devlog branch.
    checkout = git_run(["git", "checkout", "-B", branch, base_branch], repo)
    if checkout.returncode != 0:
        raise RuntimeError(checkout.stderr or checkout.stdout or "git checkout failed")

    paths = ["posts/", "docs/log/", "docs/.nojekyll", "docs/index.html"]
    add = git_run(["git", "add", "--", *paths], repo)
    if add.returncode != 0:
        raise RuntimeError(add.stderr or add.stdout or "git add failed")

    status = git_run(["git", "status", "--porcelain", "--", *paths], repo)
    if status.stdout.strip():
        msg = f"publish: devlog {day.isoformat()}"
        commit = git_run(["git", "commit", "-m", msg], repo)
        if commit.returncode != 0:
            raise RuntimeError(commit.stderr or commit.stdout or "git commit failed")

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

    # Leave the repo back on the base branch so tomorrow's run (and the user)
    # doesn't start from a leftover devlog branch.
    back = git_run(["git", "checkout", base_branch], repo)
    if back.returncode != 0:
        raise RuntimeError(back.stderr or back.stdout or "git checkout base failed")


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

    digests = collect_digests(cfg, target)
    body = generate_post(digests, model=cfg.model)

    if dry_run:
        return {
            "status": "dry_run",
            "date": target.isoformat(),
            "post": body,
            "sessions": len(digests),
            "publish_mode": cfg.publish_mode,
        }

    write_post_markdown(posts_dir, target, body, force=force)
    written = rebuild_site(repo)

    result = {
        "status": "written",
        "date": target.isoformat(),
        "post_path": str(post_path),
        "site_files": [str(p) for p in written],
        "publish_mode": cfg.publish_mode,
        "sessions": len(digests),
    }

    if cfg.publish_mode == "manual":
        result["next_steps"] = (
            f"Review {post_path}, then commit posts/ and docs/log/ and push to GitHub."
        )
        return result

    if cfg.publish_mode == "auto":
        _git_publish_auto(
            repo,
            target,
            remote=cfg.remote,
            branch=cfg.branch,
            git_run=git_run,
        )
        result["status"] = "published_auto"
        return result

    if cfg.publish_mode == "pr":
        _git_publish_pr(
            repo,
            target,
            remote=cfg.remote,
            base_branch=cfg.branch,
            git_run=git_run,
            gh_run=gh_run,
        )
        result["status"] = "published_pr"
        return result

    raise ValueError(f"Unknown publish_mode: {cfg.publish_mode!r}")


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
        "--dry-run",
        action="store_true",
        help="Generate and print; do not write files or run git",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    cfg_path = args.config or default_config_path()
    cfg = load_config(cfg_path)
    if cfg is None:
        print(f"No config at {cfg_path}. Run: devlog init")
        return 2

    try:
        target = resolve_publish_date(args.date)
    except ValueError:
        print(f"Invalid --date {args.date!r}")
        return 2

    try:
        outcome = publish_day(
            cfg,
            target,
            force=args.force,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Publish failed: {exc}")
        return 1

    if args.verbose or args.dry_run:
        print(outcome)
    else:
        print(f"{outcome.get('status')}: {outcome.get('date', target.isoformat())}")
        if outcome.get("next_steps"):
            print(outcome["next_steps"])
        if args.dry_run and outcome.get("post"):
            print()
            print(outcome["post"])
    return 0
