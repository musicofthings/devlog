# Daily Dev Log — Phase 3 Design (Publish via GitHub Pages)

**Date:** 2026-07-24  
**Status:** Approved for implementation  
**Scope:** Local nightly publish of daily posts into this repo; GitHub Pages serves a public log feed under `docs/`.

---

## 1. Problem & goal

Phase 1–2 generate a local markdown post. Phase 3 makes that post public automatically (or via a chosen review gate) on GitHub Pages, with setup through `devlog init`.

**In scope**
- `~/.config/devlog/config.toml` (Windows: `%USERPROFILE%\.config\devlog\config.toml`)
- `devlog init` (interactive + `--defaults`)
- `devlog publish` with `publish_mode`: `auto` | `pr` | `manual`
- Site builder writing `docs/log/` from `posts/*.md`
- GitHub Actions Pages deploy for `docs/`
- Optional Windows Task Scheduler registration from `init`
- Idempotent nightly: skip if `posts/YYYY-MM-DD.md` exists unless `--force`

**Out of scope**
- Recruiter portfolio polish, custom domain requirement
- Cloud Actions reading `~/.claude` / local session logs
- Cross-post to X/Substack
- Multi-user / auth
- Cursor SQLite

---

## 2. Architecture

```
devlog init → config.toml (+ optional schtasks)
     ↓
nightly: devlog publish --date yesterday
     ↓
generate post → posts/YYYY-MM-DD.md → rebuild docs/log/*
     ↓
publish_mode: auto (commit+push) | pr (branch+gh pr) | manual (files only)
     ↓
GitHub Actions → deploy docs/ to Pages
```

Local machine owns generation (session logs never leave the laptop). CI only deploys already-committed static files under `docs/`.

---

## 3. Config

```toml
sources = ["claude_code", "codex", "cursor"]
claude_root = "~/.claude"
codex_root = "~/.codex"
cursor_root = "~/.cursor"
repo_path = "C:/Users/shibi/Projects/devlog"
publish_mode = "auto"   # auto | pr | manual
schedule_time = "06:30"
remote = "origin"
branch = "main"
```

`devlog init` prompts for each field (defaults shown), writes config, prints Pages enable checklist, optionally registers a daily scheduled task.

---

## 4. CLI

| Command | Behavior |
|---------|----------|
| `devlog` / `devlog run` / legacy `main.py --date …` | Generate for date; write cwd `devlog-*.md` unless dry-run; may read config for roots/sources when flags omitted |
| `devlog init [--defaults] [--schedule/--no-schedule]` | Write config; optional scheduler |
| `devlog publish [--date yesterday\|YYYY-MM-DD] [--force] [--dry-run]` | Generate into repo `posts/`, rebuild site, apply publish_mode |

**publish_mode**
- `auto`: git add/commit/push only `posts/` + `docs/log/` (+ `.nojekyll` if needed)
- `pr`: branch `devlog/post-YYYY-MM-DD`, push, `gh pr create`
- `manual`: write files; print next steps

Idempotent: if `posts/YYYY-MM-DD.md` exists and not `--force`, skip generate/git.

Default publish date: **yesterday** (local TZ).

---

## 5. Site layout

- Keep [`docs/index.html`](docs/index.html) landing; add nav link to Log
- `posts/YYYY-MM-DD.md` — canonical markdown
- `docs/log/index.html` — reverse-chronological feed (generated)
- `docs/log/YYYY-MM-DD.html` — day page (generated)
- `docs/.nojekyll` — plain static hosting
- Generator: [`devlog/site.py`](devlog/site.py) — no Node/Jekyll

Visual language: reuse landing CSS variables (ink/paper/amber/mist).

---

## 6. GitHub Actions

`.github/workflows/pages.yml`: on push to `main` touching `docs/**` (and `workflow_dispatch`), upload `docs/` and deploy to GitHub Pages. Document enabling Pages → GitHub Actions.

Do **not** parse session logs in CI.

---

## 7. Scheduler

`devlog init` may register Windows Task Scheduler task at `schedule_time` running:

```text
python -m devlog publish --date yesterday
```

(or `python path/to/main.py`-equivalent via installed `devlog` console script).

Requires: repo_path, git/`gh` auth for auto/pr, User `ANTHROPIC_API_KEY` optional (template fallback).

---

## 8. Safety

- Publish only generated short post text, never raw transcripts
- Do not overwrite existing `posts/YYYY-MM-DD.md` without `--force`
- Init warns that project paths appear in posts

---

## 9. Acceptance

1. `devlog init` / `devlog init --defaults` creates config
2. `publish_mode` switches among auto / pr / manual
3. After auto push + Actions, day appears on Pages feed
4. Nightly re-run is idempotent without `--force`
5. Landing links to Log; site builds from fixtures in tests
