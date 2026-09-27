"""Resolve a session's working directory to a stable project identity.

`basename(cwd)` misnames projects: a session started in a subfolder, a git
worktree (`delete-post`), or the home directory (your username) each become
their own "project". The resolver walks up to the git root, names the
project after its `origin` remote, and applies user aliases from
`project_aliases` in config.toml. Git metadata is read from `.git/config`
directly; the only subprocess is `git log` for a day's commits.
"""

from __future__ import annotations

import json
import re
import shutil
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
    # How the name was chosen, for `devlog publish --explain`.
    source: str = "folder name"


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
    # GitHub CLI runner for PR lookups; None = use `gh` if it's installed.
    gh_run: GitRunner | None = None
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
            return Project(name=by_path, source="alias (path)")
        path = Path(project_path).expanduser()
        root = find_git_root(path) if path.exists() else None
        remote = read_remote_url(root) if root is not None else None
        if root is not None and root != self.home:
            remote_name = _repo_name(remote) if remote else None
            name = remote_name or root.name
            source = "git remote" if remote_name else "git root folder"
        elif path.exists() and path.resolve() == self.home.resolve():
            # Sessions in ~ are machine chores, and the folder name is your username.
            name, source = HOME_PROJECT, "home folder"
        else:
            name, source = redact_sensitive_text(basename(project_path)), "folder name"
        # Aliases may target the folder name or the derived repo name.
        folder = basename(project_path)
        aliased = self.alias(folder)
        if aliased != folder:
            name, source = aliased, "alias (folder)"
        elif self.alias(name) != name:
            name, source = self.alias(name), f"alias (of {source})"
        return Project(
            name=name,
            root=root if root is not None and root != self.home else None,
            repo_url=repo_web_url(remote) if remote else None,
            source=source,
        )

    def pull_requests(self, project: Project, day: date) -> list[dict]:
        """Your PRs in the project's GitHub repo that were opened or updated on `day`."""
        if not project.repo_url:
            return []
        runner = self.gh_run
        if runner is None:
            if shutil.which("gh") is None:
                return []
            runner = _default_git
        slug = project.repo_url.removeprefix("https://github.com/")
        cmd = ["gh", "pr", "list", "--repo", slug, "--state", "all", "--author", "@me",
               "--search", f"updated:{day.isoformat()}", "--limit", "20",
               "--json", "number,title,url,state,isDraft"]
        try:
            out = runner(cmd, project.root or Path.cwd())
        except (OSError, subprocess.SubprocessError):
            return []
        if out.returncode != 0:
            return []
        try:
            rows = json.loads(out.stdout or "[]")
        except json.JSONDecodeError:
            return []
        return [
            {"number": r["number"], "title": redact_sensitive_text(str(r.get("title", ""))),
             "url": r.get("url"),
             "state": "draft" if r.get("isDraft") and r.get("state") == "OPEN"
             else str(r.get("state", "")).lower()}
            for r in rows if isinstance(r, dict) and isinstance(r.get("number"), int)
        ]

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


def count_commits(project_paths: list[str], resolver: ProjectResolver, day: date) -> dict[str, int]:
    """Your commits that day per canonical project name (each repo counted once)."""
    counts: dict[str, int] = {}
    seen_roots: set[Path] = set()
    for path in project_paths:
        project = resolver.resolve(path)
        if project.root is None or project.root in seen_roots:
            continue
        seen_roots.add(project.root)
        n = len(resolver.commits(project, day))
        if n:
            counts[project.name] = counts.get(project.name, 0) + n
    return counts


_README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md", "Readme.md")
README_CHARS = 400


def readme_summary(root: Path) -> str | None:
    """First prose paragraph of the repo README (skips headings, badges, HTML)."""
    for name in _README_NAMES:
        path = root / name
        if path.is_file():
            break
    else:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL)
    paragraph: list[str] = []
    in_code = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        noise = (not stripped or stripped.startswith(("#", "<", "[![", "!["))
                 or set(stripped) <= set("=-*_ "))
        if noise:
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    if not paragraph:
        return None
    summary = redact_sensitive_text(" ".join(paragraph))
    return summary if len(summary) <= README_CHARS else summary[: README_CHARS - 1] + "…"


def open_pull_requests(repo_url: str, runner: GitRunner | None = None) -> list[dict]:
    """Open PRs via the GitHub CLI when it's installed and authenticated; else []."""
    if runner is None:
        if shutil.which("gh") is None:
            return []
        runner = _default_git
    slug = repo_url.removeprefix("https://github.com/")
    try:
        out = runner(["gh", "pr", "list", "--repo", slug, "--state", "open", "--limit", "10",
                      "--json", "number,title,url,isDraft"], Path.cwd())
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    try:
        rows = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return [
        {"number": r["number"], "title": redact_sensitive_text(str(r.get("title", ""))),
         "url": r.get("url"), "draft": bool(r.get("isDraft"))}
        for r in rows if isinstance(r, dict) and isinstance(r.get("number"), int)
    ]


def project_info(root: str | None, repo_url: str | None) -> dict:
    """What a project hub shows about the repo itself (README blurb, open PRs)."""
    info: dict = {}
    if root and Path(root).is_dir():
        summary = readme_summary(Path(root))
        if summary:
            info["readme"] = summary
    if repo_url:
        prs = open_pull_requests(repo_url)
        if prs:
            info["pull_requests"] = prs
    return info
