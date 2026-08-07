# Delete-Post Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the site owner remove an already-published post from the live GitHub Pages site, triggered from the site itself, without giving the public write access to the repo.

**Architecture:** A new `devlog delete --date YYYY-MM-DD` CLI command removes the post file, rebuilds the static site, and pushes the removal commit (reusing the same git plumbing `publish` already uses). A `workflow_dispatch` GitHub Actions workflow runs that command with the run's own write-scoped `GITHUB_TOKEN`. An admin panel baked into the generated `docs/log/index.html` lets the owner save a personal access token (Actions-scope only) in `localStorage` and trigger that workflow per post, with `owner/repo` auto-detected from the `origin` remote so it works unmodified on any fork.

**Tech Stack:** Python 3.11+, argparse, subprocess-driven git, vanilla JS (no build step) for the admin panel, GitHub Actions (`workflow_dispatch`).

## Global Constraints

- Python 3.11+, ruff (`line-length = 100`, `select = ["E", "F", "I", "UP", "B"]`) and pytest must stay clean throughout — verify after every task.
- No new runtime dependencies. `pyproject.toml` dependencies stay `anthropic`, `tzdata` (dev: `pytest`, `ruff`) — do not add `pyyaml` or anything else.
- No JS test harness exists in this project (`docs/assets/theme.js` has none either) — the admin panel gets a documented manual smoke-test checklist, not automated tests.
- Token scope guidance ("Actions: read and write only, not Contents") must appear in the admin panel's own copy, not just in docs.
- The delete workflow's git push uses the Action's own `GITHUB_TOKEN` (`permissions: contents: write`) — the browser-held personal token only ever triggers `workflow_dispatch`, never touches repo contents directly.
- `owner/repo` and the workflow filename are derived automatically at site-build time from `git remote get-url origin` — no new config field, no manual setup step per fork.
- Deletion is real and permanent-from-the-site-forward (a real commit removes the post); no soft-hide, no git-history rewrite. Spec: `docs/superpowers/specs/2026-08-07-delete-post-design.md`.

---

### Task 1: Extract shared git plumbing into `devlog/gitutil.py`

**Files:**
- Create: `devlog/gitutil.py`
- Modify: `devlog/publish.py` (imports, remove `GitRunner`/`_default_git`/`_git_paths`/`_git_add_and_commit` definitions, update `_git_publish_auto` and `_git_publish_pr` to call the moved functions)
- Test: existing `tests/test_publish.py` (no new tests — this is a behavior-preserving refactor; the existing 18 tests in that file are the safety net)

**Interfaces:**
- Produces (used by Task 2 and Task 3): `devlog.gitutil.GitRunner` (type alias `Callable[[list[str], Path], subprocess.CompletedProcess[str]]`), `devlog.gitutil.default_git(cmd, cwd)`, `devlog.gitutil.git_paths(repo, artifacts) -> list[str]`, `devlog.gitutil.add_and_commit(repo, message, artifacts, git_run, *, require_changes) -> bool`, `devlog.gitutil.commit_and_push(repo, message, artifacts, *, remote, branch, git_run, require_changes=False) -> bool`.

This is a pure refactor: `publish.py` currently defines its own `GitRunner`, `_default_git`, `_git_paths`, and `_git_add_and_commit`, and inlines the commit→pull-rebase→push sequence inside `_git_publish_auto`. Task 3's `delete_cmd.py` needs that exact same plumbing (stage → commit → rebase → push), and importing underscore-prefixed "private" names from `publish.py` into a sibling module would be a bad smell. Moving the git plumbing into its own module gives both `publish.py` and the new `delete_cmd.py` a clean shared dependency.

- [ ] **Step 1: Create `devlog/gitutil.py`**

```python
"""Shared subprocess-based git plumbing used by publish.py and delete_cmd.py."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

GitRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


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
    """Stage, commit, rebase onto remote, and push. Returns whether a commit was made."""
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
        raise RuntimeError(pull.stderr or pull.stdout or "git pull --rebase failed")

    push = git_run(["git", "push", remote, branch], repo)
    if push.returncode != 0:
        raise RuntimeError(push.stderr or push.stdout or "git push failed")
    return True
```

- [ ] **Step 2: Update `devlog/publish.py` imports (top of file)**

Replace lines 1-18 (from the module docstring through the `MANAGED_PATHS` constant, including the old `GitRunner`/`_default_git` block) with:

```python
"""Publish a day's post into the repo and optionally git push / open a PR."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from devlog.config import DevlogConfig, default_config_path, load_config
from devlog.digest import slice_for_date
from devlog.gitutil import GitRunner, add_and_commit, commit_and_push, default_git as _default_git
from devlog.models import RawSession
from devlog.site import rebuild_site, write_post_markdown
from devlog.summarize import generate_post

MANAGED_PATHS = ("posts/", "docs/log/", "docs/.nojekyll", "docs/index.html")
```

- [ ] **Step 3: Remove the now-moved `_git_paths` and `_git_add_and_commit` functions from `publish.py`**

Delete the `_git_paths` function definition and the `_git_add_and_commit` function definition entirely (they now live in `gitutil.py` as `git_paths` and `add_and_commit`). `_ensure_managed_paths_clean` stays in `publish.py` unchanged — it's publish-specific.

- [ ] **Step 4: Update `_git_publish_auto` to use `commit_and_push`**

Replace the whole `_git_publish_auto` function body with:

```python
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
```

- [ ] **Step 5: Update `_git_publish_pr` to use `add_and_commit`**

Inside `_git_publish_pr`'s `try` block, replace:

```python
        _git_add_and_commit(repo, day, artifacts, git_run, require_changes=True)
```

with:

```python
        add_and_commit(
            repo, f"publish: devlog {day.isoformat()}", artifacts, git_run, require_changes=True
        )
```

Leave the `written = rebuild_site(repo)` call site in `publish_day` untouched for now — `rebuild_site` doesn't take a `git_run` parameter yet, so this task's diff must not reference one. Task 2 Step 10 updates that call site once `rebuild_site` actually accepts `git_run`, keeping this task fully self-contained and green on its own.

- [ ] **Step 6: Run the full test suite and lint to confirm the refactor is behavior-preserving**

Run: `python -m pytest -q` and `python -m ruff check devlog tests`
Expected: all 74 existing tests pass unchanged, ruff reports no issues.

- [ ] **Step 7: Commit**

```bash
git add devlog/gitutil.py devlog/publish.py
git commit -m "refactor: extract shared git plumbing into devlog/gitutil.py"
```

---

### Task 2: Auto-detect the GitHub repo and render the admin panel in the feed page

**Files:**
- Modify: `devlog/site.py` (add `git_run` param to `rebuild_site`, add `detect_github_repo()`, add admin-panel HTML/CSS/JS, thread `github_repo` through `build_feed_html`)
- Test: `tests/test_publish.py` (site-builder tests already live there — add new tests alongside `test_site_builder`)

**Interfaces:**
- Consumes: `devlog.gitutil.GitRunner`, `devlog.gitutil.default_git` (from Task 1)
- Produces (used by Task 3): `devlog.site.rebuild_site(repo_path, git_run=default_git) -> list[Path]` (signature change — now accepts `git_run`), `devlog.site.detect_github_repo(repo_path, git_run=default_git) -> str | None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_publish.py` (it already has `from devlog.site import list_posts, rebuild_site, write_post_markdown` at the top — this task needs `detect_github_repo` too, imported inline in the new tests below to keep the diff small):

```python
def test_detect_github_repo_from_https_remote(tmp_path: Path):
    from devlog.site import detect_github_repo

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    def fake_git(cmd, cwd):
        from subprocess import CompletedProcess

        assert cmd == ["git", "remote", "get-url", "origin"]
        return CompletedProcess(
            cmd, 0, stdout="https://github.com/someone/theirfork.git\n", stderr=""
        )

    assert detect_github_repo(repo, git_run=fake_git) == "someone/theirfork"


def test_detect_github_repo_from_ssh_remote(tmp_path: Path):
    from devlog.site import detect_github_repo

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    def fake_git(cmd, cwd):
        from subprocess import CompletedProcess

        return CompletedProcess(cmd, 0, stdout="git@github.com:someone/theirfork.git\n", stderr="")

    assert detect_github_repo(repo, git_run=fake_git) == "someone/theirfork"


def test_detect_github_repo_returns_none_without_git_dir(tmp_path: Path):
    from devlog.site import detect_github_repo

    repo = tmp_path / "repo"
    repo.mkdir()

    def fail_git(cmd, cwd):
        raise AssertionError("git should not be invoked without a .git directory")

    assert detect_github_repo(repo, git_run=fail_git) is None


def test_detect_github_repo_returns_none_when_git_fails(tmp_path: Path):
    from devlog.site import detect_github_repo

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    def failing_git(cmd, cwd):
        from subprocess import CompletedProcess

        return CompletedProcess(cmd, 128, stdout="", stderr="fatal: no such remote 'origin'")

    assert detect_github_repo(repo, git_run=failing_git) is None


def test_feed_includes_admin_panel_when_repo_detected(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "docs" / "index.html").write_text(
        '<a href="https://github.com/musicofthings/devlog">Open on GitHub →</a>\n',
        encoding="utf-8",
    )
    write_post_markdown(repo / "posts", date(2026, 7, 20), "Built the Codex parser today.")

    def fake_git(cmd, cwd):
        from subprocess import CompletedProcess

        return CompletedProcess(cmd, 0, stdout="git@github.com:someone/theirfork.git\n", stderr="")

    rebuild_site(repo, git_run=fake_git)
    feed = (repo / "docs" / "log" / "index.html").read_text(encoding="utf-8")

    assert "someone/theirfork" in feed
    assert 'class="delete-btn" data-date="2026-07-20"' in feed
    assert "Admin: manage posts" in feed
    assert "Actions: read and write" in feed


def test_feed_omits_admin_panel_without_git_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "index.html").write_text(
        '<a href="https://github.com/musicofthings/devlog">Open on GitHub →</a>\n',
        encoding="utf-8",
    )
    write_post_markdown(repo / "posts", date(2026, 7, 20), "Built the Codex parser today.")

    rebuild_site(repo)
    feed = (repo / "docs" / "log" / "index.html").read_text(encoding="utf-8")

    assert "Admin: manage posts" not in feed
    assert "delete-btn" not in feed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_publish.py -q -k "detect_github_repo or admin_panel"`
Expected: FAIL with `ImportError: cannot import name 'detect_github_repo'` (or `TypeError: rebuild_site() got an unexpected keyword argument 'git_run'` for the feed tests) — the function/parameter doesn't exist yet.

- [ ] **Step 3: Add imports and constants to `devlog/site.py`**

At the top of `devlog/site.py`, change:

```python
from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path
```

to:

```python
from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path

from devlog.gitutil import GitRunner, default_git
```

Add this constant right after `_DATE_RE`:

```python
DELETE_WORKFLOW_FILE = "delete-post.yml"
_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$")
```

- [ ] **Step 4: Implement `detect_github_repo`**

Add this function right after `list_posts`:

```python
def detect_github_repo(repo_path: Path, git_run: GitRunner = default_git) -> str | None:
    """Best-effort 'owner/repo' derived from the origin remote, for the admin delete panel.

    Returns None (never raises) whenever detection isn't possible -- no .git
    directory, no origin remote, or a remote that isn't a github.com URL --
    so the feed just renders without the admin panel in those cases.
    """
    repo_path = Path(repo_path)
    if not (repo_path / ".git").exists():
        return None
    result = git_run(["git", "remote", "get-url", "origin"], repo_path)
    if result.returncode != 0:
        return None
    match = _GITHUB_REMOTE_RE.search(result.stdout.strip())
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"
```

- [ ] **Step 5: Run the detection tests to verify they pass**

Run: `python -m pytest tests/test_publish.py -q -k detect_github_repo`
Expected: PASS (4 tests)

- [ ] **Step 6: Add admin-panel CSS to `SHARED_CSS`**

In `devlog/site.py`, find the end of the `SHARED_CSS` string (just before the closing `"""`, right after the `.feed .excerpt { ... }` block) and insert before the closing `"""`:

```css
.admin {
  margin-top: 2.5rem;
  padding-top: 1.5rem;
  border-top: 1px dashed var(--line);
}
.admin summary {
  cursor: pointer;
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.85rem;
  color: var(--mist);
}
.admin .row {
  display: flex;
  gap: 0.5rem;
  margin: 0.75rem 0;
  flex-wrap: wrap;
}
.admin input[type="password"] {
  flex: 1;
  min-width: 12rem;
  font-family: "IBM Plex Mono", monospace;
  padding: 0.4rem 0.5rem;
}
.admin button {
  font-family: "IBM Plex Mono", monospace;
  cursor: pointer;
}
.admin .status {
  font-size: 0.8rem;
  color: var(--mist);
  margin-top: 0.5rem;
}
.feed .delete-btn {
  margin-left: 0.75rem;
  font-size: 0.78rem;
  font-family: "IBM Plex Mono", monospace;
  cursor: pointer;
}
```

- [ ] **Step 7: Implement `_admin_panel_html`**

Add this function right before `build_feed_html`:

```python
def _admin_panel_html(github_repo: str) -> str:
    return f"""
<details class="admin">
  <summary>Admin: manage posts</summary>
  <p class="status">
    Paste a GitHub fine-grained personal access token scoped to
    <code>{html.escape(github_repo)}</code> with <strong>Actions: read and write</strong>
    permission only (not Contents). It is saved in this browser's local storage and
    never sent anywhere except api.github.com.
  </p>
  <div class="row">
    <input type="password" id="devlog-token-input" placeholder="github_pat_..." autocomplete="off" />
    <button type="button" id="devlog-token-save">Save token</button>
    <button type="button" id="devlog-token-clear">Clear token</button>
  </div>
  <p class="status" id="devlog-admin-status"></p>
</details>
<script>
(function () {{
  var REPO = {json.dumps(github_repo)};
  var WORKFLOW = {json.dumps(DELETE_WORKFLOW_FILE)};
  var STORAGE_KEY = "devlog-admin-token";
  var statusEl = document.getElementById("devlog-admin-status");

  function setStatus(msg) {{
    if (statusEl) statusEl.textContent = msg;
  }}
  function getToken() {{
    try {{ return localStorage.getItem(STORAGE_KEY) || ""; }}
    catch (e) {{ return ""; }}
  }}

  var saveBtn = document.getElementById("devlog-token-save");
  var clearBtn = document.getElementById("devlog-token-clear");
  var input = document.getElementById("devlog-token-input");

  if (saveBtn) {{
    saveBtn.addEventListener("click", function () {{
      try {{
        localStorage.setItem(STORAGE_KEY, input.value.trim());
        setStatus("Token saved.");
      }} catch (e) {{
        setStatus("Could not save token: " + e);
      }}
    }});
  }}
  if (clearBtn) {{
    clearBtn.addEventListener("click", function () {{
      try {{
        localStorage.removeItem(STORAGE_KEY);
        input.value = "";
        setStatus("Token cleared.");
      }} catch (e) {{
        setStatus("Could not clear token: " + e);
      }}
    }});
  }}

  document.querySelectorAll(".delete-btn").forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      var day = btn.getAttribute("data-date");
      if (!confirm("Delete the " + day + " post? This pushes a real commit removing it.")) {{
        return;
      }}
      var token = getToken();
      if (!token) {{
        setStatus("Save a token first.");
        return;
      }}
      setStatus("Requesting delete of " + day + "...");
      fetch(
        "https://api.github.com/repos/" + REPO + "/actions/workflows/" + WORKFLOW + "/dispatches",
        {{
          method: "POST",
          headers: {{
            "Authorization": "token " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json"
          }},
          body: JSON.stringify({{ ref: "main", inputs: {{ date: day }} }})
        }}
      ).then(function (resp) {{
        if (resp.status === 204) {{
          setStatus("Delete requested for " + day + " -- refresh in about 30 seconds.");
        }} else {{
          resp.text().then(function (text) {{
            setStatus("Delete request failed (" + resp.status + "): " + text);
          }});
        }}
      }}).catch(function (err) {{
        setStatus("Delete request failed: " + err);
      }});
    }});
  }});
}})();
</script>
"""
```

- [ ] **Step 8: Thread `github_repo` through `build_feed_html`**

Replace the whole `build_feed_html` function with:

```python
def build_feed_html(posts: list[tuple[date, Path, str]], github_repo: str | None = None) -> str:
    if not posts:
        items = '<li><p class="excerpt">No posts yet.</p></li>'
    else:
        chunks: list[str] = []
        for day, _path, body in posts:
            iso = day.isoformat()
            href = f"{iso}.html"
            delete_btn = (
                f'<button type="button" class="delete-btn" data-date="{iso}">Delete</button>'
                if github_repo
                else ""
            )
            chunks.append(
                "<li>"
                f'<a href="{href}">{html.escape(iso)}</a>'
                f"{delete_btn}"
                f'<p class="excerpt">{html.escape(_excerpt(body))}</p>'
                "</li>"
            )
        items = "\n".join(chunks)
    admin_html = _admin_panel_html(github_repo) if github_repo else ""
    inner = f"""<h1>Log</h1>
<p class="meta">Reverse-chronological daily build logs</p>
<ul class="feed">
{items}
</ul>
{admin_html}
"""
    return _page("Log", inner)
```

- [ ] **Step 9: Thread `git_run` and `github_repo` through `rebuild_site`**

In `rebuild_site`, change the signature and the feed-writing lines:

```python
def rebuild_site(repo_path: Path, git_run: GitRunner = default_git) -> list[Path]:
```

and where the feed is built:

```python
    posts = list_posts(posts_dir)
    github_repo = detect_github_repo(repo_path, git_run)
    feed_path = log_dir / "index.html"
    feed_path.write_text(build_feed_html(posts, github_repo=github_repo), encoding="utf-8")
    written.append(feed_path)
```

- [ ] **Step 10: Update the `rebuild_site` call site in `devlog/publish.py`**

In `publish_day`, find `written = rebuild_site(repo)` and change it to:

```python
    written = rebuild_site(repo, git_run=git_run)
```

This threads whatever `git_run` `publish_day` was called with (real subprocess by default, or a test's fake) down into the new repo-detection call inside `rebuild_site`.

- [ ] **Step 11: Run the full test suite and lint**

Run: `python -m pytest -q` and `python -m ruff check devlog tests`
Expected: all tests pass (74 pre-existing + 6 new = 80), ruff clean.

- [ ] **Step 12: Commit**

```bash
git add devlog/site.py devlog/publish.py tests/test_publish.py
git commit -m "feat: auto-detect GitHub repo and add delete-post admin panel to the feed page"
```

---

### Task 3: `devlog delete` CLI command

**Files:**
- Create: `devlog/delete_cmd.py`
- Test: Create `tests/test_delete.py`

**Interfaces:**
- Consumes: `devlog.gitutil.GitRunner`, `devlog.gitutil.default_git`, `devlog.gitutil.commit_and_push` (Task 1); `devlog.site.rebuild_site(repo, git_run=...)` (Task 2); `devlog.config.DevlogConfig`, `default_config_path`, `load_config`, `save_config` (existing)
- Produces (used by Task 4): `devlog.delete_cmd.delete_day(cfg, target, *, dry_run=False, git_run=default_git) -> dict`, `devlog.delete_cmd.cmd_delete(argv=None) -> int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_delete.py`:

```python
from datetime import date
from pathlib import Path

import pytest

from devlog.config import DevlogConfig, save_config
from devlog.delete_cmd import cmd_delete, delete_day
from devlog.site import rebuild_site, write_post_markdown


def _repo_with_post(tmp_path: Path, day: date, body: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "docs" / "index.html").write_text(
        '<a href="https://github.com/musicofthings/devlog">Open on GitHub →</a>\n',
        encoding="utf-8",
    )
    write_post_markdown(repo / "posts", day, body)
    rebuild_site(repo)
    return repo


def test_delete_missing_post_raises(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = DevlogConfig(repo_path=str(repo).replace("\\", "/"))

    with pytest.raises(FileNotFoundError, match="2026-07-20"):
        delete_day(cfg, date(2026, 7, 20))


def test_delete_dry_run_touches_nothing(tmp_path: Path):
    repo = _repo_with_post(tmp_path, date(2026, 7, 20), "Built the parser.")
    cfg = DevlogConfig(repo_path=str(repo).replace("\\", "/"))

    out = delete_day(cfg, date(2026, 7, 20), dry_run=True)

    assert out["status"] == "dry_run"
    assert (repo / "posts" / "2026-07-20.md").exists()


def test_delete_removes_post_prunes_html_and_pushes(tmp_path: Path):
    repo = _repo_with_post(tmp_path, date(2026, 7, 20), "Built the parser.")
    cfg = DevlogConfig(repo_path=str(repo).replace("\\", "/"), remote="origin", branch="main")
    calls: list[list[str]] = []

    def fake_git(cmd: list[str], cwd: Path):
        calls.append(cmd)
        from subprocess import CompletedProcess

        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="D posts/2026-07-20.md\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    out = delete_day(cfg, date(2026, 7, 20), git_run=fake_git)

    assert out["status"] == "deleted"
    assert not (repo / "posts" / "2026-07-20.md").exists()
    assert not (repo / "docs" / "log" / "2026-07-20.html").exists()
    feed = (repo / "docs" / "log" / "index.html").read_text(encoding="utf-8")
    assert "2026-07-20" not in feed
    commit_call = next(c for c in calls if c[:2] == ["git", "commit"])
    assert commit_call[-1] == "delete: devlog 2026-07-20"
    assert any(c[:2] == ["git", "push"] for c in calls)


def test_delete_raises_if_nothing_to_commit(tmp_path: Path):
    """Guards against a silently-empty commit if the post was never tracked by git."""
    repo = _repo_with_post(tmp_path, date(2026, 7, 20), "Built the parser.")
    cfg = DevlogConfig(repo_path=str(repo).replace("\\", "/"))

    def fake_git(cmd: list[str], cwd: Path):
        from subprocess import CompletedProcess

        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    with pytest.raises(RuntimeError, match="No generated changes"):
        delete_day(cfg, date(2026, 7, 20), git_run=fake_git)


def test_cmd_delete_missing_post_exits_2(tmp_path: Path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg_path = tmp_path / "config.toml"
    save_config(DevlogConfig(repo_path=str(repo).replace("\\", "/")), cfg_path)

    code = cmd_delete(["--date", "2026-07-20", "--config", str(cfg_path)])

    assert code == 2
    assert "2026-07-20" in capsys.readouterr().out
    assert not (repo / "posts").exists()


def test_cmd_delete_invalid_date_exits_2(tmp_path: Path, capsys):
    repo = _repo_with_post(tmp_path, date(2026, 7, 20), "Built the parser.")
    cfg_path = tmp_path / "config.toml"
    save_config(DevlogConfig(repo_path=str(repo).replace("\\", "/")), cfg_path)

    code = cmd_delete(["--date", "not-a-date", "--config", str(cfg_path)])

    assert code == 2
    assert "Invalid --date" in capsys.readouterr().out


def test_cmd_delete_no_config_exits_2(tmp_path: Path, capsys):
    missing_cfg = tmp_path / "nope.toml"

    code = cmd_delete(["--date", "2026-07-20", "--config", str(missing_cfg)])

    assert code == 2
    assert "No config" in capsys.readouterr().out


def test_cmd_delete_dry_run_prints_status(tmp_path: Path, capsys):
    repo = _repo_with_post(tmp_path, date(2026, 7, 20), "Built the parser.")
    cfg_path = tmp_path / "config.toml"
    save_config(DevlogConfig(repo_path=str(repo).replace("\\", "/")), cfg_path)

    code = cmd_delete(["--date", "2026-07-20", "--dry-run", "--config", str(cfg_path)])

    assert code == 0
    assert "dry_run: 2026-07-20" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_delete.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'devlog.delete_cmd'`

- [ ] **Step 3: Implement `devlog/delete_cmd.py`**

```python
"""Delete a previously published daily post and push the removal."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from devlog.config import DevlogConfig, default_config_path, load_config
from devlog.gitutil import GitRunner, commit_and_push, default_git
from devlog.site import rebuild_site


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
    written = rebuild_site(repo, git_run=git_run)
    artifacts = [post_path, *written]

    commit_and_push(
        repo,
        f"delete: devlog {target.isoformat()}",
        artifacts,
        remote=cfg.remote,
        branch=cfg.branch,
        git_run=git_run,
        require_changes=True,
    )

    return {
        "status": "deleted",
        "date": target.isoformat(),
        "post_path": str(post_path),
    }


def cmd_delete(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete a previously published daily post")
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_delete.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the full suite and lint**

Run: `python -m pytest -q` and `python -m ruff check devlog tests`
Expected: all tests pass (80 + 8 = 88), ruff clean.

- [ ] **Step 6: Commit**

```bash
git add devlog/delete_cmd.py tests/test_delete.py
git commit -m "feat: add devlog delete CLI command"
```

---

### Task 4: Wire `delete` into the `devlog` CLI dispatch

**Files:**
- Modify: `devlog/cli.py:131-143` (the `main()` function)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `devlog.delete_cmd.cmd_delete` (Task 3)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_main_dispatches_delete_subcommand(monkeypatch):
    import devlog.delete_cmd

    calls = []

    def fake_cmd_delete(argv):
        calls.append(argv)
        return 0

    monkeypatch.setattr(devlog.delete_cmd, "cmd_delete", fake_cmd_delete)

    code = main(["delete", "--date", "2026-07-20"])

    assert code == 0
    assert calls == [["--date", "2026-07-20"]]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_cli.py -q -k dispatches_delete`
Expected: FAIL — `main(["delete", ...])` currently falls through to `cmd_run`, so it either errors on the unexpected `--date`/positional handling or returns without calling `fake_cmd_delete` (`calls == []`), failing the `assert calls == [...]` line.

- [ ] **Step 3: Add the dispatch branch in `devlog/cli.py`**

In `main()`, add a new branch between the `"publish"` branch and the `{"run", "generate"}` branch:

```python
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "init":
        from devlog.init_cmd import cmd_init

        return cmd_init(argv[1:])
    if argv and argv[0] == "publish":
        from devlog.publish import cmd_publish

        return cmd_publish(argv[1:])
    if argv and argv[0] == "delete":
        from devlog.delete_cmd import cmd_delete

        return cmd_delete(argv[1:])
    if argv and argv[0] in {"run", "generate"}:
        return cmd_run(argv[1:])
    return cmd_run(argv)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_cli.py -q -k dispatches_delete`
Expected: PASS

- [ ] **Step 5: Run the full suite and lint**

Run: `python -m pytest -q` and `python -m ruff check devlog tests`
Expected: all tests pass (88 + 1 = 89), ruff clean.

- [ ] **Step 6: Manual smoke check**

Run: `devlog delete --help` (or `python main.py` doesn't expose `delete` since `main.py` only wraps the default run-parser path — use the installed `devlog` console command, or `python -c "from devlog.cli import main; main(['delete', '--help'])"`)
Expected: argparse help text listing `--date`, `--config`, `--dry-run`, `--verbose`, no traceback.

- [ ] **Step 7: Commit**

```bash
git add devlog/cli.py tests/test_cli.py
git commit -m "feat: wire devlog delete into the CLI dispatch"
```

---

### Task 5: GitHub Actions delete-post workflow

**Files:**
- Create: `.github/workflows/delete-post.yml`

**Interfaces:**
- Consumes: `devlog delete --date <input>` (Task 3), triggered via the admin panel's `fetch()` call (Task 2)

This task has no pytest coverage — it's infrastructure config, matching how `.github/workflows/pages.yml` and `ci.yml` also have no automated tests. Verification is a manual read-through plus (optionally, once merged) a real trigger from the Actions tab.

- [ ] **Step 1: Create `.github/workflows/delete-post.yml`**

```yaml
name: Delete devlog post

# Triggered from the admin panel on docs/log/index.html via the GitHub API's
# workflow_dispatch endpoint, using a personal token scoped to Actions:
# read/write only. This job's own GITHUB_TOKEN does the actual commit/push,
# so that personal token never needs repo-contents write access.

on:
  workflow_dispatch:
    inputs:
      date:
        description: "Post date to delete (YYYY-MM-DD)"
        required: true

permissions:
  contents: write

jobs:
  delete:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install
        run: python -m pip install -e .

      - name: Delete post
        run: devlog delete --date "${{ github.event.inputs.date }}"
        env:
          GIT_AUTHOR_NAME: devlog-bot
          GIT_AUTHOR_EMAIL: devlog-bot@users.noreply.github.com
          GIT_COMMITTER_NAME: devlog-bot
          GIT_COMMITTER_EMAIL: devlog-bot@users.noreply.github.com
```

- [ ] **Step 2: Verify the YAML is well-formed**

Run: `python -c "import tomllib" 2>/dev/null; cat .github/workflows/delete-post.yml`
Read the file back and confirm: 2-space indentation throughout, no tabs, the `on.workflow_dispatch.inputs.date.required` key is `true` (unquoted YAML boolean), and it structurally mirrors `.github/workflows/ci.yml`'s `checkout` → `setup-python` → install → run steps. (This repo has no YAML linter dependency — a careful visual diff against `ci.yml`'s structure is the check.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/delete-post.yml
git commit -m "feat: add delete-post GitHub Actions workflow"
```

---

### Task 6: Document the feature and final verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- None (documentation only)

- [ ] **Step 1: Add a "Delete a published post" section to `README.md`**

Find the `## Publish (Phase 3)` section's closing content (the "Public URLs after deploy" bullet list, ending with the `docs/log/YYYY-MM-DD.html` line) and insert a new section immediately after it, before `## Manual acceptance`:

```markdown
## Delete a published post (Phase 4)

`publish_mode = auto` means posts go public with no review, so there's a way to take one back down without touching the machine that owns the repo:

```bash
devlog delete --date 2026-07-20          # removes posts/2026-07-20.md, rebuilds the site, commits, pushes
devlog delete --date 2026-07-20 --dry-run
```

The same thing is available from the live site itself: `docs/log/index.html` renders an "Admin: manage posts" panel (only when the repo's `origin` remote points at GitHub — auto-detected, no config needed). Paste a GitHub **fine-grained personal access token scoped to this repo, with Actions: read and write permission only** (not Contents) into the token field — it's saved in your browser's local storage and never sent anywhere except `api.github.com`. Clicking Delete on a post triggers `.github/workflows/delete-post.yml`, which runs `devlog delete` with the workflow's own repo-write credentials — your personal token only ever needs permission to trigger the workflow, never to write repository contents directly.

Deletion is real: it's a normal commit removing `posts/YYYY-MM-DD.md` and rebuilding `docs/log/`. It's recoverable from git history on a full clone, but gone from the live site and any future clone going forward.
```

- [ ] **Step 2: Update the `## Layout` section's file tree**

Find the `devlog/` block in the `## Layout` code fence and add the two new files in alphabetical order:

```
devlog/
  cli.py              # argparse entrypoint (per-source roots)
  delete_cmd.py        # devlog delete: remove a published post + push the removal
  digest.py           # calendar-day slicing (local timezone) + compact digests
  gitutil.py           # shared git add/commit/push plumbing (publish + delete)
  models.py            # RawSession, SessionDigest
  summarize.py        # digest → post (Claude API or template fallback)
```

(Keep the existing `sources/`, `evals/`, `main.py`, `sample_data/`, `tests/`, `docs/` lines unchanged below it.)

- [ ] **Step 3: Verify the CLI flags table still matches actual argparse output**

Run: `devlog delete --help` and `python -m pytest tests/test_delete.py -q -k cmd_delete` to confirm the flags documented in Step 1 (`--date`, `--dry-run`) match the real `cmd_delete` parser from Task 3.
Expected: help text matches; the 3 `cmd_delete`-prefixed tests pass.

- [ ] **Step 4: Run the complete suite one more time**

Run: `python -m pytest -q` and `python -m ruff check devlog tests`
Expected: 89 tests pass, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document devlog delete and the admin delete panel"
```

- [ ] **Step 6: Manual smoke-test checklist (browser side — no automated coverage exists for this)**

Run these once after all tasks land and are pushed, against a real fork or this repo once Pages redeploys:
1. Load `docs/log/index.html` with no token saved — confirm the page layout is unchanged and no delete action is possible.
2. Save a token in the Admin panel, reload the page, confirm the token persisted (still shows as saved — check via browser devtools `localStorage.getItem("devlog-admin-token")`).
3. Click Delete with a token that has the wrong scope (e.g. no Actions permission) — confirm the inline status shows a non-204 response instead of failing silently.
4. Click Delete with a valid token on a throwaway test post — confirm a run appears under the repo's Actions tab for `Delete devlog post`, and once it completes, the post is gone from `main` and the live feed.
