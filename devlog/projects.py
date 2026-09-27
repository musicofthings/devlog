"""Resolve a session's working directory to a stable project identity.

`basename(cwd)` misnames projects: a session started in a subfolder, a git
worktree (`delete-post`), or the home directory (your username) each become
their own "project". The resolver walks up to the git root, names the
project after its `origin` remote, and applies user aliases from
`project_aliases` in config.toml. Git metadata is read from `.git/config`
directly; the only subprocess is `git log` for a day's commits.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from devlog.digest import basename
from devlog.privacy import redact_sensitive_text

MAX_COMMITS = 25
HOME_PROJECT = "home"

GitRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]
_REMOTE_RE = re.compile(r'^\[remote "(?P<name>[^"]+)"\]\s*$')
_GITHUB_RE = re.compile(r"github\.com[:/](?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$")
_FIELD_SEP = "\x1f"


def _default_git(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True, timeout=20)


@dataclass
class Project:
    name: str
    root: Path | None = None
    repo_url: str | None = None


def repo_web_url(remote_url: str) -> str | None:
    match = _GITHUB_RE.search(remote_url.strip())
    if match is None:
        return None
    return f"https://github.com/{match.group('owner')}/{match.group('repo')}"


def _repo_name(remote_url: str) -> str | None:
    tail = remote_url.strip().rstrip("/").replace(":", "/").split("/")[-1]
    tail = tail[:-4] if tail.endswith(".git") else tail
    return tail or None


def find_git_root(path: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _git_dir(root: Path) -> Path | None:
    """The directory holding `config`: follows worktree `.git` files."""
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git
    try:
        pointer = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not pointer.startswith("gitdir:"):
        return None
    gitdir = Path(pointer.split(":", 1)[1].strip())
    if not gitdir.is_absolute():
        gitdir = (root / gitdir).resolve()
    commondir = gitdir / "commondir"
    if commondir.is_file():
        try:
            gitdir = (gitdir / commondir.read_text(encoding="utf-8").strip()).resolve()
        except OSError:
            pass
    return gitdir


def read_remote_url(root: Path, remote: str = "origin") -> str | None:
    git_dir = _git_dir(root)
    if git_dir is None:
        return None
    try:
        lines = (git_dir / "config").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    in_remote = False
    for line in lines:
        header = _REMOTE_RE.match(line.strip())
        if header:
            in_remote = header.group("name") == remote
            continue
        if line.strip().startswith("["):
            in_remote = False
            continue
        if in_remote and "=" in line:
            key, _, value = line.partition("=")
            if key.strip() == "url":
                return value.strip()
    return None


@dataclass
class ProjectResolver:
    """Canonical project names. `aliases` keys match names or paths, case-insensitively."""

    aliases: dict[str, str] = field(default_factory=dict)
    git_run: GitRunner = _default_git
    home: Path = field(default_factory=Path.home)
    _cache: dict[str, Project] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._aliases = {
            k.replace("\\", "/").rstrip("/").lower(): v for k, v in self.aliases.items()
        }

    def alias(self, name: str) -> str:
        return self._aliases.get(name.replace("\\", "/").rstrip("/").lower(), name)

    def resolve(self, project_path: str) -> Project:
        cached = self._cache.get(project_path)
        if cached is not None:
            return cached
        project = self._resolve(project_path)
        self._cache[project_path] = project
        return project

    def _resolve(self, project_path: str) -> Project:
        raw = project_path.replace("\\", "/").rstrip("/")
        by_path = self._aliases.get(raw.lower())
        if by_path:
            return Project(name=by_path)
        path = Path(project_path).expanduser()
        root = find_git_root(path) if path.exists() else None
        remote = read_remote_url(root) if root is not None else None
        if root is not None and root != self.home:
            name = (_repo_name(remote) if remote else None) or root.name
        elif path.exists() and path.resolve() == self.home.resolve():
            # Sessions in ~ are machine chores, and the folder name is your username.
            name = HOME_PROJECT
        else:
            name = redact_sensitive_text(basename(project_path))
        # Aliases may target the folder name or the derived repo name.
        folder = basename(project_path)
        aliased = self.alias(folder)
        name = aliased if aliased != folder else self.alias(name)
        return Project(
            name=name,
            root=root if root is not None and root != self.home else None,
            repo_url=repo_web_url(remote) if remote else None,
        )

    def commits(self, project: Project, day: date) -> list[dict]:
        """Your commits in the project's repo on `day` (local time), newest first."""
        if project.root is None:
            return []
        start = datetime.combine(day, time.min).astimezone()
        end = start + timedelta(days=1)
        try:
            email = self.git_run(["git", "config", "user.email"], project.root)
        except (OSError, subprocess.SubprocessError):
            return []  # git not installed / not runnable
        author = email.stdout.strip() if email.returncode == 0 else ""
        cmd = [
            "git", "log", "--all", "--no-merges", f"--max-count={MAX_COMMITS}",
            f"--since={start.isoformat()}", f"--until={end.isoformat()}",
            f"--format=%H{_FIELD_SEP}%h{_FIELD_SEP}%aI{_FIELD_SEP}%s",
        ]
        if author:
            cmd.insert(3, f"--author={author}")
        try:
            out = self.git_run(cmd, project.root)
        except (OSError, subprocess.SubprocessError):
            return []
        if out.returncode != 0:
            return []
        commits: list[dict] = []
        for line in out.stdout.splitlines():
            parts = line.split(_FIELD_SEP)
            if len(parts) != 4:
                continue
            sha, short, when, subject = parts
            commits.append(
                {
                    "sha": sha,
                    "short": short,
                    "time": when,
                    "subject": redact_sensitive_text(subject.strip()),
                    "url": f"{project.repo_url}/commit/{sha}" if project.repo_url else None,
                }
            )
        return commits
