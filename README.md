# Daily Dev Log

Turns local AI coding session history into one short, factual, first-person
"build log" post per day, published to a GitHub Pages site. Reads Claude
Code, Codex, and Cursor session transcripts and merges them into one post.

**Repo:** https://github.com/musicofthings/devlog

## Install

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
```

This installs a `devlog` console command (`devlog run`, `devlog init`,
`devlog publish`, `devlog delete`) — everything below works with either
`devlog` or `python main.py` interchangeably; `python main.py` needs no
install step.

## Use

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

## Publish automatically

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

`devlog init` also makes a best-effort attempt to turn on Task Scheduler's operational event log, so a future disappearance leaves a diagnosable trail next time. This needs admin elevation, which `devlog init` does not have by default, so it will usually print a note that it couldn't. To enable it yourself, open **PowerShell as Administrator** (a regular PowerShell window is not enough, even one you opened yourself) and run:

```powershell
wevtutil sl "Microsoft-Windows-TaskScheduler/Operational" /e:true
```

### Troubleshooting: the `devlog` command stops working entirely

`devlog init`'s scheduling step verifies the exact Python that will run the nightly task can actually `import devlog` *before* registering anything, so a broken install is caught immediately with a clear error instead of failing silently at 06:30. This specifically catches a stale editable install — e.g. running `pip install -e .` from a temporary checkout and later deleting it, which orphans the install and breaks `devlog` everywhere, not just the scheduled task. If you ever see `ModuleNotFoundError: No module named 'devlog'` from the `devlog` command itself, check where the editable install actually points:

```bash
pip show devlog   # look at "Editable project location"
```

If it points somewhere that no longer exists, reinstall from the real repo checkout: `pip install -e ".[dev]"`.

## Delete a published post

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

## Slash commands for AI coding assistants

If you use Claude Code, Codex, Cursor, or Grok Build to work in a repo with devlog installed, you can drive it with `/devlog-init`, `/devlog-publish`, `/devlog-delete`, and `/devlog-status` instead of typing the CLI commands yourself. Each command just tells the assistant which `devlog` commands to run and how to handle the output (e.g. `/devlog-delete` always confirms with you before running the real, non-dry-run delete).

| Tool | Where the commands live | Setup needed |
|------|--------------------------|--------------|
| **Claude Code** | `.claude/commands/*.md` | None — auto-discovered from the repo. |
| **Cursor** | `.cursor/skills/devlog-*/SKILL.md` | None — auto-discovered from the repo. |
| **Grok Build** | `.grok/skills/devlog-*/SKILL.md` | None — auto-discovered from the repo. |
| **Codex CLI** | `.codex/prompts/*.md` (shipped in-repo) | Codex only reads prompts from `~/.codex/prompts/`, not the repo — copy or symlink them: `cp .codex/prompts/*.md ~/.codex/prompts/` |

Codex's custom-prompts mechanism is marked deprecated in OpenAI's own docs (in favor of a "Skills" system) but still works as documented at the time this was written; if it stops working, check `.codex/prompts/*.md` here against current Codex CLI docs.
