# Daily Dev Log — Phase 2

Turns local AI coding session history into one short, factual, first-person
"build log" post per day. Phase 2 ships Claude Code, Codex, and Cursor
(agent-transcript) source plugins; a default run merges all three into one post.

**Repo:** https://github.com/musicofthings/devlog

## Results (verified locally)

| Suite | Result |
|-------|--------|
| `pytest` | **106 passed** |
| Offline evals (`python -m evals.run`) | **8/8 passed** |
| Live evals (`python -m evals.run --live`) | **8/8 passed** |

Token-efficient Claude path: compact, redacted digests with `src=` tags,
empty-day API skip, explicit external-API consent, and a 5-sentence output clamp.

## Layout

```
devlog/
  cli.py              # argparse entrypoint (per-source roots)
  delete_cmd.py       # devlog delete: remove a published post + push the removal
  digest.py           # calendar-day slicing (local timezone) + compact digests
  gitutil.py          # shared git add/commit/push plumbing (publish + delete)
  models.py           # RawSession, SessionDigest
  status.py           # .devlog-status.json: last publish/delete, shown on the feed page
  summarize.py        # digest → post (Claude API or template fallback)
  sources/
    base.py           # plugin registry
    claude_code.py    # ~/.claude projects JSONL
    codex.py          # ~/.codex sessions/YYYY/MM/DD/rollout-*.jsonl
    cursor.py         # ~/.cursor/projects/*/agent-transcripts/**/*.jsonl
evals/                # acceptance-style offline/live eval harness
main.py               # thin wrapper → devlog.cli
sample_data/          # claude_code, codex, cursor fixtures
tests/                # pytest suite
docs/                 # GitHub Pages landing + superpowers specs/plans
```

## Setup

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
```

This also installs a `devlog` console command (`devlog run`, `devlog init`,
`devlog publish`, `devlog delete`) — everything below works with either `devlog` or
`python main.py` interchangeably; `python main.py` needs no install step.

## Run

Print today's post without writing a file (defaults: all three sources):

```bash
python main.py --date today --dry-run
# or, once installed:
devlog --date today --dry-run
```

Against bundled sample data:

```bash
python main.py --date 2026-07-22 --sources claude_code --claude-root sample_data/claude_code
python main.py --date 2026-07-20 --sources codex,cursor \
  --codex-root sample_data/codex --cursor-root sample_data/cursor --dry-run
```

Against real local logs:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # optional
python main.py --date today --dry-run --verbose --allow-external-api
```

External API use is disabled by default even when a key is present. Enable it
with `--allow-external-api` for a run or set `allow_external_api = true` in the
config file. Transcript-derived text is redacted before it leaves the local
pipeline. Writes `devlog-YYYY-MM-DD.md` unless `--dry-run` is set, and refuses
to replace an existing post unless `--force` is supplied.

### CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--date` | `today` | Target day (`YYYY-MM-DD` or `today`, local timezone) |
| `--sources` | `claude_code,codex,cursor` | Comma-separated source plugins |
| `--claude-root` | `~/.claude` | Claude Code data root |
| `--codex-root` | `~/.codex` | Codex data root |
| `--cursor-root` | `~/.cursor` | Cursor data root (agent transcripts) |
| `--sample-mode` | off | Optional/legacy; Claude sample layout is auto-detected |
| `--dry-run` | off | Print post; do not write `devlog-*.md` |
| `--force` | off | Replace an existing generated post |
| `--allow-external-api` | off | Permit redacted transcript text to be sent to the model API |
| `--verbose` | off | Extra diagnostics per source |

Missing roots are skipped (other sources still run).

## Tests

```bash
python -m pytest
# expected: 106 passed
```

## Evals

```bash
python -m evals.run          # expected: 8/8
python -m evals.run --json
python -m evals.run --live   # requires ANTHROPIC_API_KEY
```

Also covered by pytest via `tests/test_evals.py`.

## Publish (Phase 3)

Initialize config (writes `%USERPROFILE%\.config\devlog\config.toml`):

```bash
devlog init --defaults          # non-interactive
devlog init                     # prompts; can register Task Scheduler
```

Publish yesterday's post into `posts/` + rebuild `docs/log/`:

```bash
devlog publish --dry-run
devlog publish                  # uses publish_mode from config: auto | pr | manual
devlog publish --date 2026-07-20 --force
```

Publishing always runs locally — your session transcripts never leave this machine, so there's no "publish" button on the website. To publish on demand instead of waiting for the nightly schedule, either run `devlog publish` yourself, or double-click the `Publish Devlog Now.cmd` shortcut `devlog init` writes to your Desktop (opens a window, shows the result, waits for a keypress so you actually see it).

Enable GitHub Pages: repo **Settings → Pages → Source: GitHub Actions**
(workflow: `.github/workflows/pages.yml` uploads `docs/` as the site root).

Public URLs after deploy:

- Landing: https://musicofthings.github.io/devlog/
- Log feed: https://musicofthings.github.io/devlog/log/
- Day post: https://musicofthings.github.io/devlog/log/YYYY-MM-DD.html

### Troubleshooting: the scheduled task silently stops running

If `devlog init` registers the `DailyDevLogPublish` Windows Scheduled Task, `%LOCALAPPDATA%\devlog\publish.log` should gain a new entry every night. If posts stop appearing and the log stops growing, check whether the task still exists at all:

```powershell
schtasks /Query /TN DailyDevLogPublish /V /FO LIST
```

`ERROR: The system cannot find the file specified` means the task was removed — Windows Task Scheduler does not keep a history of *why* by default, so there's usually no trail explaining it. Re-run `devlog init` (with `--schedule` if you're not doing the interactive prompts) to register it again.

One confirmed cause, already fixed: `pytest` used to call the real `unregister_windows_task()` (via `cmd_init`'s `--no-schedule` path in `test_init_defaults`) with no test seam, so simply running the test suite on a machine that had the task registered would silently delete it. The test now mocks that call. If you're developing devlog itself, keep an eye out for any new test that exercises `cmd_init`, `register_windows_task`, or `unregister_windows_task` without mocking them — those are the only functions in this codebase allowed to touch the real Windows Task Scheduler.

`devlog init` also makes a best-effort attempt to turn on Task Scheduler's operational event log, so a future disappearance leaves a diagnosable trail next time. This needs admin elevation, which `devlog init` does not have by default, so it will usually print a note that it couldn't. To enable it yourself, open **PowerShell as Administrator** (a regular PowerShell window is not enough, even one you opened yourself) and run:

```powershell
wevtutil sl "Microsoft-Windows-TaskScheduler/Operational" /e:true
```

### Troubleshooting: the `devlog` command stops working entirely

`devlog init`'s scheduling step now verifies the exact Python that will run the nightly task can actually `import devlog` *before* registering anything, so a broken install is caught immediately with a clear error instead of failing silently at 06:30. This specifically catches a stale editable install — e.g. running `pip install -e .` from a temporary checkout or worktree and later deleting it, which orphans the install and breaks `devlog` everywhere, not just the scheduled task. If you ever see `ModuleNotFoundError: No module named 'devlog'` from the `devlog` command itself, check where the editable install actually points:

```bash
pip show devlog   # look at "Editable project location"
```

If it points somewhere that no longer exists, reinstall from the real repo checkout: `pip install -e ".[dev]"`.

## Delete a published post (Phase 4)

`publish_mode = auto` means posts go public with no review, so there's a way to take one back down without touching the machine that owns the repo:

```bash
devlog delete --date 2026-07-20          # removes posts/2026-07-20.md, rebuilds the site, commits, pushes
devlog delete --date 2026-07-20 --dry-run
```

The same thing is available from the live site itself: `docs/log/index.html` renders an "Admin: manage posts" panel (only when the repo's `origin` remote points at GitHub — auto-detected, no config needed). Paste a GitHub **fine-grained personal access token scoped to this repo, with Actions: read and write permission only** (not Contents) into the token field — it's saved in your browser's local storage and never sent anywhere except `api.github.com`. Clicking Delete on a post triggers `.github/workflows/delete-post.yml`, which runs `devlog delete` with the workflow's own repo-write credentials — your personal token only ever needs permission to trigger the workflow, never to write repository contents directly.

The admin panel is collapsed by default. Clicking Delete (or clicking Save token with nothing entered) automatically expands it and scrolls it into view so the result — "Save a token first", "Delete requested…", or an error — is always visible; it doesn't stay hidden just because the panel happened to load closed.

Deletion is real: it's a normal commit removing `posts/YYYY-MM-DD.md` and rebuilding `docs/log/`. It's recoverable from git history on a full clone, but gone from the live site and any future clone going forward.

### Troubleshooting: Delete fails with `403 Resource not accessible by personal access token`

The most common cause, confirmed in practice: when creating the fine-grained token at https://github.com/settings/personal-access-tokens, the **Repository access** radio button was left on **"Public Repositories (read-only)"** instead of switched to **"Only select repositories"** with this repo picked. That option silently forces the whole token to read-only no matter what you set the Actions permission checkbox to below it — Actions still shows "Read and write" in the UI, but the token can't actually write anything. Fix: create a new token with Repository access set to "Only select repositories," this repo selected, and Actions permission "Read and write." The admin panel's inline error message for a 403 repeats this same check.

### Knowing what actually happened

The feed page shows a small status line — "Last published: 2026-08-06 (2026-08-07 06:30 UTC) · Last deleted: 2026-07-19 (2026-08-07 07:03 UTC)" — sourced from a small `.devlog-status.json` file at the repo root that both `devlog publish` and `devlog delete` update as part of their normal commit. It only appears once at least one publish or delete has actually happened; there's nothing to show on a brand-new site.

## Manual acceptance

1. `python main.py --date 2026-07-20 --dry-run --verbose` (or another day with real activity).
2. Confirm Codex paths use real `cwd` and Cursor paths decode from `projects/` folder names.
3. Confirm a multi-tool day produces one merged post with distinct sources in digests (`--verbose`).
4. `devlog init --defaults` then `devlog publish --date <fixture-day> --dry-run`.

## Next

- Recruiter-facing portfolio polish and custom domain.
- Optional Cursor SQLite / Composer history (deferred; agent transcripts only for now).

See `PRD_TRD.md` and `docs/superpowers/` for full spec and design.
