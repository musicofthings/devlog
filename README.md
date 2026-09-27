# Daily Dev Log

Turns your local AI coding session history (Claude Code, Codex, Cursor, Grok, Copilot CLI, OpenCode, and more) into two things:

1. **A public build log.** One short, factual, first-person post per day, published to GitHub Pages. You control how much of your prompts it reveals.
2. **A private second brain in Obsidian.** Every day is linked to its projects, kinds of work, topics, commits, open follow-ups, weeks, months, and quarters. Coding agents can query it through an MCP server.

Transcripts never leave your machine unless you explicitly allow the (redacted) Claude API summarizer.

**Repo:** https://github.com/musicofthings/devlog · **Site:** https://musicofthings.github.io/devlog/ · **Design notes and roadmap:** [`REVIEW_AND_ROADMAP.md`](REVIEW_AND_ROADMAP.md)

## What you get

| | |
|---|---|
| **Nightly public post** | Minutes, projects, the kinds of work, commits shipped, and your stack. Three privacy levels plus your own redaction regexes (MRNs, sample IDs, …); quiet days aren't published. |
| **Obsidian knowledge graph** | Day notes linked to project, work-type, and topic hubs; weekly, monthly, and quarterly rollups; a Home dashboard with streaks and a heatmap; Bases views and per-project canvases. |
| **What happened, not just how long** | Your commits that day, open threads captured from agent recaps (tick them off in Obsidian), related days, repo README and open PRs on each project hub. |
| **Tokens and cost** | Tokens per model, and an API-equivalent cost estimate per day, project, and period. |
| **Local-first extras** | Optional [Ollama](https://ollama.com) models for semantic related days, period retros, and writing the post itself. None of it leaves your machine. |
| **Agent memory** | `devlog mcp` lets Claude Code or Codex ask "what did I do on this project, and what's still open?" |
| **Safe automation** | A nightly Windows scheduled task; `review`/`pr`/`manual` publish modes; rollback that never discards your uncommitted work; hide or delete a post from the live site. |

## Install

Requires Python 3.11+.

```bash
pip install -e ".[dev]"          # add ,mcp for the agent-memory server: ".[dev,mcp]"
devlog init                      # config, Obsidian vault detection, nightly schedule
```

This installs a `devlog` command: `devlog run` (the default), `init`, `publish`, `hide`, `unhide`, `delete`, `obsidian`, and `mcp`. Everything below also works as `python main.py …`, which needs no install step.

## Use

Print today's post without writing a file (defaults: all registered sources):

```bash
python main.py --date today --dry-run
# or, once installed:
devlog --date today --dry-run
```

Against bundled sample data:

```bash
python main.py --date 2026-07-22 --sources claude_code --claude-root sample_data/claude_code
python main.py --date 2026-07-20 --sources codex,cursor,grok,copilot \
  --codex-root sample_data/codex --cursor-root sample_data/cursor \
  --grok-root sample_data/grok --copilot-root sample_data/copilot --dry-run
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
| `--sources` | `claude_code,codex,cursor,grok,copilot,opencode,warp,vitreous,antigravity` | Comma-separated source plugins |
| `--claude-root` | `~/.claude` | Claude Code data root |
| `--codex-root` | `~/.codex` | Codex data root |
| `--cursor-root` | `~/.cursor` | Cursor data root (agent transcripts) |
| `--grok-root` | `~/.grok` | Grok CLI data root |
| `--copilot-root` | `~/.copilot` | GitHub Copilot CLI data root |
| `--opencode-root` | `%LOCALAPPDATA%/opencode` (Windows) or `~/.local/share/opencode` | OpenCode data dir (`opencode.db`) |
| `--warp-root` | `%LOCALAPPDATA%/warp/Warp` | Warp data root (`data/warp.sqlite`) |
| `--vitreous-root` | `~/.vitreous` | Vitreous sessions root (JSONL when persisted) |
| `--antigravity-root` | `~/.gemini` | Antigravity / Gemini data root |
| `--sample-mode` | off | Optional/legacy; Claude sample layout is auto-detected |
| `--dry-run` | off | Print post; do not write `devlog-*.md` |
| `--force` | off | Replace an existing generated post |
| `--allow-external-api` | off | Permit redacted transcript text to be sent to the model API |
| `--verbose` | off | Extra diagnostics per source |

Missing roots are skipped (other sources still run). Empty stores (Warp with cloud-only history, OpenCode not installed, Vitreous before persistence ships) yield no sessions and are not errors.

### Source plugins

| Source | Status | Default root | Notes |
|--------|--------|--------------|-------|
| `claude_code` | Real parser | `~/.claude` | `projects/*/*.jsonl` |
| `codex` | Real parser | `~/.codex` | `sessions/**/rollout-*.jsonl` |
| `cursor` | Real parser | `~/.cursor` | Agent transcripts only (no VS Code SQLite) |
| `grok` | Real parser | `~/.grok` | `sessions/<url-encoded-cwd>/<uuid>/chat_history.jsonl` |
| `copilot` | Real parser | `~/.copilot` | `session-state/<uuid>/events.jsonl` |
| `opencode` | Skip-empty | OS data dir | SQLite `opencode.db` (legacy JSON fallback) |
| `warp` | Skip-empty | `%LOCALAPPDATA%/warp/Warp` | Local SQLite; 0 rows if cloud storage is on |
| `vitreous` | Skip-empty | `~/.vitreous` | Looks for `sessions/*.jsonl`; persistence not shipped yet. Does not parse `nvidia-skills`. |
| `antigravity` | Deferred | `~/.gemini` | Conversations are protobuf/encrypted; no fake decoder. Plaintext `.jsonl` is parsed if present. |

Not installed here and not stubbed: Aider, gemini-cli, Cline, Continue, Windsurf, Amazon Q, Amp, Crush, Goose.

## Publish automatically

Initialize config (writes `%USERPROFILE%\.config\devlog\config.toml`):

```bash
devlog init --defaults          # non-interactive
devlog init                     # prompts; can register Task Scheduler
```

Publish yesterday's post into `posts/` + rebuild `docs/log/`:

```bash
devlog publish --dry-run
devlog publish                  # uses publish_mode from config: auto | pr | manual | review
devlog publish --date 2026-07-20 --force
devlog publish --confirm --date 2026-07-20   # push an already-written review-mode post
```

Publishing always runs locally — your session transcripts never leave this machine, so there's no "publish" button on the website. To publish on demand instead of waiting for the nightly schedule, either run `devlog publish` yourself, or double-click the `Publish Devlog Now.cmd` shortcut `devlog init` writes to your Desktop (opens a window, shows the result, waits for a keypress so you actually see it).

With `publish_mode = review`, the nightly job writes `posts/` + `docs/log/` but does not push. After you edit the markdown, run `devlog publish --confirm --date YYYY-MM-DD` to commit and push (same recovery as auto if push fails).

### Privacy: what reaches the public site

Posts are public, and your prompts can contain things that shouldn't be (patient or sample identifiers, client names, unpublished results). Three settings control this. The private Obsidian vault always keeps full detail.

```toml
public_detail = "projects"      # summary | projects | verbatim
redact_patterns = ['MRN\d{6}', 'S-\d{4}-\d+', '(?i)acme corp']
publish_empty_days = false
```

- `public_detail = "projects"` (default): project names plus generic work types, how many commits you shipped, and a "Stack" line of recognized tools and libraries from the built-in catalog (never your custom topics). For example: *"Work: code-review and git-ops on vitreous. Shipped 3 commit(s). Stack: Python, scanpy."* No prompt text, file names, commands, or commit messages.
- `public_detail = "summary"`: minutes, a project count, work types, and the commit count only.
- `public_detail = "verbatim"`: the previous behavior, which quotes the first prompt per project.

Project names in posts come from the git remote and `project_aliases`, the same as in the vault. Sessions started in your home folder appear as `home`, not your username.

The same level applies to the digest sent to the LLM when `allow_external_api` is on.

`redact_patterns` are your own regexes. Matches become `[REDACTED]` everywhere redaction runs: posts, the LLM digest, and vault notes. Use single-quoted TOML strings so backslashes stay literal.

With `publish_empty_days = false`, a day with no activity is not committed (`skipped_empty`), but it is still written to the vault so streaks and weekly notes stay accurate.

### Offline Obsidian vault

GitHub Pages stays the public site. Each successful local `posts/` write also mirrors into a private Obsidian vault (archive note + Daily Note embed) when `obsidian_vault` is set. Vault notes are **never** git-managed and are **preserved by default** on hide/delete.

`devlog init` (including `--defaults`) auto-detects the vault currently open in Obsidian (`%APPDATA%\obsidian\obsidian.json`). If none exists, it creates `~/Documents/DevLog` as a new vault (`.obsidian` + `DevLog/` + `Daily/`) and registers it in Obsidian when that config file is present. Interactive init pre-fills the detected or proposed path; blank the field to skip.

```toml
obsidian_vault = "C:/Users/you/Documents/DevLog"   # filled by init
obsidian_folder = "DevLog"
obsidian_daily_folder = "Daily"
obsidian_on_delete = "preserve"   # preserve | remove

# Optional: fold folder/worktree/repo names into one project (vault only).
# Keys match a folder name, a repo name, or a full path, case-insensitively.
[project_aliases]
"delete-post" = "devlog"      # a git worktree folder
"window" = "devlog"
```

Projects are identified by walking up from each session's working directory to its git root and naming the project after the `origin` remote (worktrees resolve to their main repo). Sessions started in your home folder become a `home` project instead of your username. Folders outside git fall back to the folder name. `project_aliases` is applied on top.

Layout after publish — the vault is a linked knowledge graph, not a pile of disconnected days:

| Note | What it holds | Links to |
|------|---------------|----------|
| `DevLog/YYYY-MM-DD.md` | Day note: the post, topics, then per-project detail: what you asked for, **your commits that day** (linked to GitHub), files, tools, **tokens and cost**, and **open threads** as checkboxes; ends with **related days** | its projects, work types, topics, week, month, prev/next active day, Home |
| `DevLog/Projects/<project>.md` | Project hub: repo link, **README summary**, **open pull requests**, **open threads**, active days, minutes, commits, tokens and cost, work mix, topics, frequently touched files, canvas link, timeline table | every day the project was worked on |
| `DevLog/Work/<type>.md` | Work-type hub (`code-review`, `planning`, `bugfix`, `feature`, `refactor`, `testing`, `docs`, `ui-ux`, `git-ops`, `devops`, `data-analysis`, `learning`, `research`) | every day and project with that kind of work |
| `DevLog/Weekly/YYYY-Www.md` | Weekly rollup: minutes and commits per project, tokens, work mix, quiet days | its days, projects, month(s), prev/next week |
| `DevLog/Monthly/YYYY-MM.md` | Monthly rollup, same shape as weekly, plus its weeks | its days, weeks, quarter, projects, prev/next month |
| `DevLog/Quarterly/YYYY-Qn.md` | Quarterly rollup, same shape, plus its months | its days, months, projects, prev/next quarter |
| `DevLog/DevLog Home.md` | Dashboard: totals, streaks, API-equivalent cost, projects (with open-thread counts), work types, topics, recent days, quarters, months, weeks, an embedded Bases view, a Heatmap Calendar block, optional Dataview queries | everything |
| `DevLog/Topics/<topic>.md` | Topic hub (scanpy, Nextflow, GATK, variant calling, FHIR, PyTorch, … or your own): days, projects, co-occurring topics, and a **Literature & notes** area that's never overwritten — put Zotero citekeys here | every day/project where the topic came up |
| `DevLog/Canvas/<project>.canvas` | JSON Canvas: project hub → last 12 active days → top topics. Regenerated until you rearrange it; then it's yours (delete to regenerate) | hub, days, topics |
| `DevLog/DevLog.base` | Obsidian **Bases** views (Days, Projects, Weeks) over the note properties — no community plugin needed. Written once; edit it freely | — |
| `Daily/YYYY-MM-DD.md` | Your daily note; devlog only upserts an embed between `%% devlog:daily:start %%` / `%% devlog:daily:end %%` | the day note |

Obsidian features used:

- **Properties** (`type`, `date`, `week`, `month`, `active_minutes`, `projects`, `work_types`, `topics`, `commits`, `open_threads`, `cost_usd`, `sources`). Links inside properties count in the graph and backlinks, and work with Dataview and Bases.
- **Nested tags** (`#devlog/project/<name>`, `#devlog/work/<type>`, `#devlog/topic/<topic>`, `#devlog/project-hub`, `#devlog/work-hub`, `#devlog/topic-hub`, `#devlog/week`) for tag-pane browsing and graph filters.
- **Backlinks / graph view** — hubs are real notes, so "every day I touched vitreous" is the hub's backlinks. New vaults created by `devlog init` get graph color groups for hubs.
- **Callouts, embeds, tables** — hubs render without plugins; Dataview queries on Home are optional (collapsed callout).

Work types are classified locally and deterministically from your prompts (keyword rules in `devlog/worktypes.py`, with a tool-mix fallback) — nothing is sent anywhere. Project names are case-folded (`Gurukul` and `gurukul` share one hub; the other spelling becomes an alias). Harness-injected text (MCP tool manifests, AGENTS.md bootstraps, skill preambles) is filtered out of both posts and notes.

**Open threads.** When an agent ends a turn with a "Next steps" / "Follow-ups" / "TODO" list (or `- [ ]` items), those items are captured locally from the Claude Code, Codex, Cursor, Copilot, Grok, and OpenCode transcripts and shown as checkboxes on the day note and as an *Open threads* list on the project hub. Tick one in either place (plain checkbox or the Tasks plugin) and it stays ticked across regenerations and disappears from the hub; untick it in the day note to reopen it. Commits come from `git log` in each project's repo, limited to your `user.email`. Assistant text, commits, and tokens only ever go to the vault, never to the public post.

**Topics, related days, reviews.** Topics are detected locally from what you asked, commit subjects, open threads, and file names, using a built-in catalog (bioinformatics, workflow, clinical, data/ML, languages, frameworks) plus your own:

```toml
[topics]
"CRISPR screens" = ["crispr", "sgrna", "mageck"]
"Variant interpretation" = ["acmg", "clinvar", "pathogenic"]
```

Topics are recomputed on each refresh, so `devlog obsidian --reindex` applies a new topic to your whole history. Each day note gets **Related days** (local TF-IDF similarity, with no model and no network, showing the shared terms). Weekly, monthly, and quarterly notes get a **Review** section (time vs. the previous period, new projects, first-time topics, threads raised), and Home shows your current/longest **streak** and a Heatmap Calendar block.

**Cost, quarters, and repo context.** Claude Code and Codex logs record which model produced each turn, so day notes, hubs, rollups, and Home show an *API-equivalent* cost estimate (`cost_usd` is also a property, for Bases/Dataview). Built-in rates cover current Claude models. Anything else is left "unpriced" until you add it:

```toml
[model_prices]                 # USD per million tokens: [input, output, cache_read]
"gpt-5-codex" = [1.25, 10.0, 0.125]
```

Quarterly rollups (`DevLog/Quarterly/`) sit above the monthly ones. Project hubs show the first paragraph of the repo README and, if the GitHub CLI (`gh`) is installed and logged in, the open pull requests.

**Optional local models.** Two features can use an [Ollama](https://ollama.com) server on your own machine. Transcript text never goes to an external API, results are cached in `DevLog/.devlog/`, and if Ollama isn't running devlog falls back silently:

```toml
related_backend = "ollama"              # semantic "Related days" via local embeddings
ollama_embed_model = "nomic-embed-text"
period_retros = true                    # a short retro in weekly and monthly notes
ollama_model = "llama3.2"
ollama_url = "http://localhost:11434"
```

**Agent memory (MCP).** `devlog mcp` serves the vault index to coding agents over stdio (read-only), so a new session can recall prior work before starting:

```bash
pip install -e ".[mcp]"
claude mcp add devlog -- devlog mcp      # Claude Code
codex mcp add devlog -- devlog mcp       # Codex CLI
```

Tools: `list_projects`, `recent_activity(days, project)`, `project_status(project)`, `open_threads(project)`, `search_log(query)`, `day_log(date)`. The index is reread on every call, so a long-running server sees each night's publish.

**Your notes are safe.** Every generated note is a managed block ending in `%% devlog:end %%`; anything you write below that line (in day notes, hubs, weeklies, Home) is preserved on every refresh. Hubs with no remaining days are deleted only if you never wrote in them. A day note you delete by hand in Obsidian is not recreated. Per-day metadata lives in `DevLog/.devlog/index.json` (hidden from Obsidian); hubs are regenerated from it and only changed files are rewritten.

Backfill posts that already exist in the repo (run once after upgrading — it also migrates old Daily Note embeds, which were wrapped in a `%%` comment and therefore invisible in Reading view):

```bash
devlog obsidian --backfill --dry-run
devlog obsidian --backfill            # metadata recovered from post text
devlog obsidian --backfill --rescan   # re-read local session logs for full per-project detail
devlog obsidian --date 2026-08-13
devlog obsidian --reindex             # regenerate hubs / weeklies / Home from the index only
```

Recommended Obsidian plugins: none are required. **Bases** (core) or **Dataview** for live tables; **Calendar** + **Periodic Notes** for the Daily/Weekly layout; **Tasks** for open threads; **Heatmap Calendar** for the Home heatmap; **Zotero Integration** for citekeys in topic hubs.

Hard delete leaves vault notes alone unless `obsidian_on_delete = remove` or you pass `--also-obsidian`. Soft-hide never touches Obsidian. A missing vault path warns and does not fail GitHub publish.

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

## Hide or delete a published post

`publish_mode = auto` means posts go public with no review, so there's a way to take one back down without touching the machine that owns the repo.

Soft-hide (preferred when you may want the post back): keeps `posts/YYYY-MM-DD.md`, removes the day from the public feed:

```bash
devlog hide --date 2026-07-20
devlog unhide --date 2026-07-20
devlog hide --date 2026-07-20 --dry-run
```

Hard delete (real git removal of the markdown):

```bash
devlog delete --date 2026-07-20          # removes posts/2026-07-20.md, rebuilds the site, commits, pushes
devlog delete --date 2026-07-20 --dry-run
devlog delete --date 2026-07-20 --also-obsidian   # also remove the vault archive + Daily Note embed
```

The same actions are available from the live site itself: `docs/log/index.html` renders an "Admin: manage posts" panel (only when the repo's `origin` remote points at GitHub — auto-detected, no config needed). Paste a GitHub **fine-grained personal access token scoped to this repo, with Actions: read and write permission only** (not Contents) into the token field — it's saved in your browser's local storage and never sent anywhere except `api.github.com`. Clicking Hide / Unhide / Delete on a post triggers `.github/workflows/delete-post.yml` (inputs: `date`, optional `action`), which runs the matching `devlog` command with the workflow's own repo-write credentials — your personal token only ever needs permission to trigger the workflow, never to write repository contents directly. After dispatch, the panel polls the Actions run until it completes.

The admin panel is collapsed by default. Clicking Hide/Delete (or clicking Save token with nothing entered) automatically expands it and scrolls it into view so the result is always visible.

Deletion is real: it's a normal commit removing `posts/YYYY-MM-DD.md` and rebuilding `docs/log/`. Soft-hide is reversible via `unhide` without rewriting history.

### Troubleshooting: Delete fails with `403 Resource not accessible by personal access token`

The most common cause, confirmed in practice: when creating the fine-grained token at https://github.com/settings/personal-access-tokens, the **Repository access** radio button was left on **"Public Repositories (read-only)"** instead of switched to **"Only select repositories"** with this repo picked. That option silently forces the whole token to read-only no matter what you set the Actions permission checkbox to below it — Actions still shows "Read and write" in the UI, but the token can't actually write anything. Fix: create a new token with Repository access set to "Only select repositories," this repo selected, and Actions permission "Read and write." The admin panel's inline error message for a 403 repeats this same check.

### Knowing what actually happened

The feed page shows a small status line — "Last published: 2026-08-06 (2026-08-07 06:30 UTC) · Last deleted: 2026-07-19 (2026-08-07 07:03 UTC)" — sourced from a small `.devlog-status.json` file at the repo root that both `devlog publish` and `devlog delete` update as part of their normal commit. It only appears once at least one publish or delete has actually happened; there's nothing to show on a brand-new site.

## Slash commands for AI coding assistants

If you use Claude Code, Codex, Cursor, or Grok Build to work in a repo with devlog installed, you can drive it with `/devlog-init`, `/devlog-publish`, `/devlog-delete`, `/devlog-hide`, `/devlog-unhide`, `/devlog-status`, and `/devlog-obsidian` instead of typing the CLI commands yourself. Each command just tells the assistant which `devlog` commands to run and how to handle the output (e.g. `/devlog-delete` and `/devlog-hide` always confirm with you before running the real, non-dry-run action).

All five surfaces are generated from one source per command in `commands/`. Edit `commands/<name>.md`, then run `python -m devlog.commands_sync`. CI fails if the generated files are stale (`--check`).

| Tool | Where the commands live | Setup needed |
|------|--------------------------|--------------|
| **Claude Code** | `.claude/commands/*.md` | None — auto-discovered from the repo. |
| **Cursor** | `.cursor/skills/devlog-*/SKILL.md` | None — auto-discovered from the repo. |
| **Grok Build** | `.grok/skills/devlog-*/SKILL.md` | None — auto-discovered from the repo (`user-invocable`). |
| **Codex CLI** | `.agents/skills/devlog-*/SKILL.md` | None — auto-discovered from the repo. |

Codex CLI 0.117+ removed custom prompts (`~/.codex/prompts` / `/prompts:…`). The in-repo `.codex/prompts/*.md` files are kept only as legacy references; live Codex support is the `.agents/skills/` copies. If Codex warns that the skills context budget was exceeded, invoke a skill by path (e.g. “use `.agents/skills/devlog-status/SKILL.md`”) or trim unused global skills under `~/.agents/skills`.
