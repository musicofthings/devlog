# Daily Dev Log — Delete-Post Design

**Date:** 2026-08-07
**Status:** Approved for implementation
**Scope:** Let the site owner remove an already-published post from the live GitHub Pages site, triggered from the site itself, without giving the public write access to the repo. Ships as a generic feature — works on any fork once its `origin` remote points at that fork's own GitHub repo.

---

## 1. Problem & goal

Phase 3 turned on `publish_mode = "auto"`: every night's post now goes straight to `main` and onto the public site with no human review. That raises the odds that something unwanted (a post that shouldn't have been public, a redaction miss) ends up live. There is currently no way to take a post back down except editing git history by hand on the machine that owns the repo.

**In scope**
- `devlog delete --date YYYY-MM-DD` CLI command
- `.github/workflows/delete-post.yml` — `workflow_dispatch` workflow that runs the command with repo-write credentials
- An admin panel embedded in the generated `docs/log/index.html` feed page: save a GitHub token locally, click Delete per post, which dispatches the workflow
- Automatic `owner/repo` detection from `git remote get-url origin` at site-build time, so the feature works unmodified on any fork

**Out of scope**
- Full git-history purge (rewriting/force-pushing history) — a real delete commit is enough; recoverability via git history is treated as acceptable, not a bug
- Soft-hide / undo — deletion is real and immediate once the workflow runs
- Live status/progress reporting in the browser while the workflow runs
- Any auth model besides "site owner holds a personal access token" — no multi-user permissions, no shared password
- Automated tests for the browser-side JS (this project has no JS test harness today; `docs/assets/theme.js` is untested for the same reason)

---

## 2. Architecture

```
docs/log/index.html (feed page)
  → visitor pastes their own GitHub PAT, stored in localStorage only
  → clicks Delete on a post → confirm() → fetch()
     POST /repos/{owner}/{repo}/actions/workflows/delete-post.yml/dispatches
     Authorization: token <PAT>   (scope: Actions read/write only)
        ↓
.github/workflows/delete-post.yml  (workflow_dispatch, input: date)
  → actions/checkout, setup-python, pip install -e .
  → devlog delete --date <input>
        ↓
devlog delete (devlog/delete_cmd.py)
  → unlink posts/YYYY-MM-DD.md
  → rebuild_site(repo)   [already prunes stale docs/log/YYYY-MM-DD.html + regenerates feed]
  → git add / commit / push   (same tail as publish.py's auto-publish flow)
        ↓
main branch updated → GitHub Pages redeploys via push-triggered pages.yml
```

`owner/repo` is not stored in config. `rebuild_site()` derives it once per build from `git remote get-url origin` and bakes it into the generated feed page as a small inline constant, so forks need zero configuration for this feature beyond having their own `origin` set correctly (which every git clone already requires).

---

## 3. `devlog delete` CLI

```
devlog delete --date YYYY-MM-DD [--config PATH] [--dry-run]
```

| Behavior | Detail |
|---|---|
| Missing post | If `posts/YYYY-MM-DD.md` doesn't exist, exit 2 with a clear message. |
| Dry run | Prints what would be removed; does not touch files or git. |
| Delete | Unlinks the post file, calls `rebuild_site(repo)` (existing function — already removes the now-stale `docs/log/YYYY-MM-DD.html` and rewrites `docs/log/index.html`), then stages and commits via shared `gitutil.commit_and_push`. If commit succeeds but pull/push fails, the local delete commit is auto-reset (`git reset --hard HEAD~1`). If failure is pre-commit, the post body and site HTML are restored in the working tree. |
| Publish mode | Reads `remote`/`branch` from config the same way `publish` does. Runs regardless of `publish_mode` (manual/auto/pr) — deleting isn't gated by the same review-before-publish concern that motivated `publish_mode`, since the whole point is taking something back down quickly. |

Implementation reuses, not duplicates: `_git_add_and_commit` and the commit→pull-rebase→push sequence move to shared functions in `publish.py` (or are called from there) so `delete_cmd.py` has no independent git implementation.

---

## 4. GitHub Actions workflow

`.github/workflows/delete-post.yml`:

```yaml
name: Delete devlog post
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
      - uses: actions/checkout@v4
      - name: Validate date
        env:
          DELETE_DATE: ${{ github.event.inputs.date }}
        run: |
          if [[ ! "$DELETE_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
            echo "Invalid date input: $DELETE_DATE"; exit 1
          fi
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e .
      - run: devlog delete --date "$DELETE_DATE"
        env:
          DELETE_DATE: ${{ github.event.inputs.date }}
          GIT_AUTHOR_NAME: devlog-bot
          GIT_AUTHOR_EMAIL: devlog-bot@users.noreply.github.com
          GIT_COMMITTER_NAME: devlog-bot
          GIT_COMMITTER_EMAIL: devlog-bot@users.noreply.github.com
```

The run's own `GITHUB_TOKEN` (scoped by `permissions: contents: write`) does the push via the checkout action's persisted credentials — the personal token from the browser never touches repository contents directly, only triggers this workflow. Pages redeploy is left to push-triggered `pages.yml` (same concurrency group is not shared with this workflow, so deploys are not cancelled by delete).

---

## 5. Frontend admin panel

Added to `build_feed_html()` in `devlog/site.py`, rendered only on `docs/log/index.html`:

- A collapsed "Admin" section: a token input + Save/Clear buttons. Saving writes to `localStorage["devlog-admin-token"]`; nothing is sent anywhere until a delete is clicked.
- Each feed entry gets a Delete button. Clicking it:
  1. `confirm("Delete the <date> post? This pushes a real commit removing it.")`
  2. Reads the token from `localStorage`. If absent, shows an inline message to save one first instead of failing silently.
  3. `fetch()`s the workflow-dispatch endpoint with the stored token.
  4. On a non-2xx response, shows the response status inline (e.g. bad token, wrong scope). On success, shows "Delete requested — refresh in about 30 seconds."
- `owner/repo` and workflow filename are baked in as a small inline `<script>const DEVLOG_REPO = "owner/repo";</script>` emitted by `rebuild_site()`, not hardcoded to this project.
- No visitor without a valid token can do anything beyond seeing buttons that fail with a 401/403 from GitHub's API — there is no default or shared secret embedded in the page.

---

## 6. Safety

- Recommended token scope, documented directly next to the token input on the page: a **fine-grained PAT scoped to this one repo, Actions: read and write only** — explicitly not Contents. Worst case on leak: someone can trigger workflows already defined in the repo, not write arbitrary files.
- `devlog delete` performs a real, permanent-from-the-site-forward removal. Recoverable only via git history on a full clone — this is called out in the CLI's `--help` text and in `pages_checklist()`'s existing privacy note in `init_cmd.py`.
- The delete workflow requires no interaction with local session logs (`~/.claude`, `~/.codex`, `~/.cursor`) at all — consistent with the existing rule that CI never touches transcript sources (Phase 3 spec, §6).

---

## 7. Testing

- `devlog delete`: pytest coverage mirroring `test_publish.py`'s fixtures (fake `git_run`, `tmp_path` repo) —
  - deletes an existing post and the feed no longer lists it
  - stale `docs/log/YYYY-MM-DD.html` is pruned
  - commit + push calls happen in the right order (same assertions style as `test_publish_auto_calls_git`)
  - missing-post date exits 2 with a clear message, no git calls made
  - `--dry-run` touches neither files nor git
- `rebuild_site()`'s new owner/repo detection: unit test with a `tmp_path` git repo carrying a fake `origin` remote, asserting the derived `owner/repo` string appears in the generated feed HTML.
- Browser-side JS: no automated test (matches this project's existing convention — `theme.js` has none either). Manual smoke-test checklist to run once after implementation:
  1. Load the feed page, confirm no admin controls change layout when no token is saved.
  2. Save a token, confirm it persists across reload (localStorage).
  3. Click Delete with an intentionally-scoped-wrong token, confirm the 403 is shown inline rather than failing silently.
  4. Click Delete with a valid token on a throwaway test post, confirm the workflow run appears in the repo's Actions tab and the post is gone from `main` after it completes.

---

## 8. Acceptance

1. `devlog delete --date <existing-day>` removes the post locally and pushes the removal commit.
2. `devlog delete --date <missing-day>` exits 2, touches nothing.
3. Triggering the workflow from the browser with a valid Actions-scoped token results in the post disappearing from the live site within a couple of minutes.
4. A fork with its own `origin` gets a working delete panel with zero code changes, once `delete-post.yml` and Pages are set up per the existing `pages_checklist()` instructions.
5. `pytest` and `ruff` stay green.
