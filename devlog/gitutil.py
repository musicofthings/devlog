"""Shared subprocess-based git plumbing used by publish.py and delete_cmd.py."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

GitRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


class GitPublishError(RuntimeError):
    """Raised when commit/pull/push fails after staging work.

    ``committed`` is True when a local commit already exists (so recovery
    needs ``git reset``, not ``git checkout`` of a working-tree path).
    """

    def __init__(self, message: str, *, committed: bool = False) -> None:
        super().__init__(message)
        self.committed = committed


def default_git(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def git_paths(repo: Path, artifacts: list[Path]) -> list[str]:
    """Return unique repo-relative paths suitable for pathspec-safe git calls."""
    paths: list[str] = []
    for artifact in artifacts:
        try:
            relative = artifact.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError as exc:
            raise RuntimeError(f"Generated artifact is outside the repository: {artifact}") from exc
        if relative not in paths:
            paths.append(relative)
    return paths


def add_and_commit(
    repo: Path,
    message: str,
    artifacts: list[Path],
    git_run: GitRunner,
    *,
    require_changes: bool,
) -> bool:
    """Stage artifacts and commit if there's anything new.

    Returns whether a commit was made. Raises if require_changes is set and
    there's nothing to commit.
    """
    paths = git_paths(repo, artifacts)
    if not paths:
        if require_changes:
            raise RuntimeError("No generated changes to publish")
        return False
    add = git_run(["git", "add", "--", *paths], repo)
    if add.returncode != 0:
        raise RuntimeError(add.stderr or add.stdout or "git add failed")

    status = git_run(["git", "status", "--porcelain", "--", *paths], repo)
    if status.returncode != 0:
        raise RuntimeError(status.stderr or status.stdout or "git status failed")
    if not status.stdout.strip():
        if require_changes:
            raise RuntimeError("No generated changes to publish")
        return False

    commit = git_run(["git", "commit", "-m", message], repo)
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr or commit.stdout or "git commit failed")
    return True


def commit_and_push(
    repo: Path,
    message: str,
    artifacts: list[Path],
    *,
    remote: str,
    branch: str,
    git_run: GitRunner,
    require_changes: bool = False,
) -> bool:
    """Stage, commit, rebase onto remote, and push. Returns whether a commit was made.

    Raises ``GitPublishError`` with ``committed=True`` if pull/push fails after
    a local commit was created. Pre-commit failures raise plain ``RuntimeError``.
    """
    if not add_and_commit(repo, message, artifacts, git_run, require_changes=require_changes):
        return False

    # Rebase onto the remote first so a scheduled push doesn't fail forever
    # after the remote moved (e.g. an edit made on GitHub or another machine).
    pull = git_run(["git", "pull", "--rebase", remote, branch], repo)
    if pull.returncode != 0:
        # Leave the repo in a clean state on failure -- otherwise it's stuck
        # mid-rebase and every subsequent scheduled run fails too, until a
        # human runs `git rebase --abort` by hand.
        git_run(["git", "rebase", "--abort"], repo)
        raise GitPublishError(
            pull.stderr or pull.stdout or "git pull --rebase failed",
            committed=True,
        )

    push = git_run(["git", "push", remote, branch], repo)
    if push.returncode != 0:
        raise GitPublishError(
            push.stderr or push.stdout or "git push failed",
            committed=True,
        )
    return True
