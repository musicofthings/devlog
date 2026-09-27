# devlog — code review & next-phase roadmap (2026-09)

Scope: full read of `devlog/` (≈5.6k LOC, 9 source parsers), tests (250 passing), CI, and the
44 published posts in `posts/`. Findings are ranked by impact. ✅ = fixed in this change.

## What this change delivers: the vault is now a knowledge graph

Before, each mirrored day was an island: one archive note with `tags: [devlog]` and nothing
linking it to anything else. Now every publish/backfill regenerates a linked graph (see README →
*Offline Obsidian vault*):

- **Day notes** get properties (`projects`, `work_types`, `week`, `active_minutes`, `sources`)
  as wikilinks, nested tags, prev/next navigation between active days, and a per-project
  section: what you asked for, files touched, tools, and minutes.
- **Project hubs** (`DevLog/Projects/`), **work-type hubs** (`DevLog/Work/`),
  **weekly rollups** (`DevLog/Weekly/`), and a **Home dashboard** (optional Dataview queries).
- Work types are classified locally (`devlog/worktypes.py`), including a `data-analysis` type
  for NGS / single-cell / pipeline work.
- Everything you write below `%% devlog:end %%` survives regeneration. Old day notes you
  edited are kept on upgrade too.
- New modules: `noise.py` (filters injected prompts), `worktypes.py`, `knowledge.py` (per-day
  metadata), `vault_graph.py` (rendering). `obsidian.py` now uses them.

## Review findings

### High

1. ✅ **Harness-injected text leaks into public posts.** Some agent CLIs write their own
   context into the transcript as if the user typed it. That text ended up on the public site
   as the day's "task": `<mcp_meta_tools> You have access to MCP…` (2026-08-08, 08-12),
   `# AGENTS.md instructions for ~\OneDrive\…` (08-16, 09-24, which also exposes a local path
   layout), and `Base directory for this skill: ~\.claude\plugins\…` (08-17). Fix:
   `noise.is_injected_prompt` is applied in `digest.slice_for_date`, so every source is covered.
   Continuation nudges such as "Try again" or "resume session" are no longer picked as the
   headline task. *Already-published posts are unchanged.* Regenerate them with
   `devlog publish --date … --force` if you want them cleaned.
2. ✅ **The Daily Note embed never rendered.** The old region was `%%devlog\n![[…]]\n%%`.
   `%% … %%` is an Obsidian comment, so the embed was hidden in Reading view and Live Preview.
   The embed now sits between separate `%% devlog:daily:start %%` / `%% devlog:daily:end %%`
   markers, and old regions are migrated in place.
3. ✅ **Verbatim prompts go to a public site.** The template post quotes your first prompt per
   project word for word (e.g. "help me clean up disk space…", "remove all mcp servers…").
   In a clinical or research setting a prompt can contain patient identifiers, sample IDs, or
   unpublished results. `privacy.py` only redacts API keys and the home path. Recommendations:
   - Add a `public_detail = "summary" | "projects" | "verbatim"` setting. The public post gets
     the reduced form; the vault always keeps full detail.
   - Add a user regex denylist (MRNs, sample-ID patterns, client names).
   - Default nightly jobs to `review` mode for anyone working with clinical data.
4. ✅ **`git reset --hard HEAD~1` in rollback can destroy unrelated work.**
   `publish._restore_failed_publish` and `delete_cmd._restore_failed_delete` hard-reset the
   whole checkout. `_ensure_managed_paths_clean` only checks `posts/`, `docs/log/` etc., so
   uncommitted edits elsewhere in the repo (for example, devlog code you're working on) are
   wiped when a nightly push fails. Use `git reset --keep HEAD~1`, which aborts instead of
   clobbering, or a soft reset plus a checkout of only the managed paths. Update the matching
   assertions in `tests/test_publish.py` and `tests/test_delete.py`.

### Medium

5. ✅ **Every run re-parses your entire history.** Each `iter_sessions` walks and parses every
   transcript ever written (e.g. `sources/claude_code.py:212`). The cost grows without bound.
   Pass a `since` date and skip files whose mtime is older than the target day minus 1.
   `obsidian --rescan` already batches this into one pass.
6. ✅ **Project identity is `basename(cwd)`.** That yields `shibi` (your home directory),
   `window`, and `delete-post` (a worktree or branch folder) as "projects", and it split
   `Gurukul` from `gurukul`. Case is now merged in the vault. Next steps: resolve the cwd to the
   git top-level folder and the `origin` repo name, and add a `[project_aliases]` table in
   `config.toml`.
7. ✅ **Empty days clutter the feed and history.** 20 of 44 posts say "No coding activity
   logged today." Add `publish_empty_days = false`. The vault already treats these as "quiet
   days" and navigation skips them.
8. ✅ **Template posts are low-signal** ("Tools: Read (104x), StrReplace (92x)"), and the
   LLM path is off by default for privacy. Options:
   - A local model backend (Ollama or llama.cpp) so summarization never leaves the machine.
   - A richer template: say what changed (files and commits), not how many tool calls ran.
   - *Shipped:* posts now say "Shipped N commit(s)." and, at `projects` detail, list the stack
     (catalog tools and libraries) instead of raw tool counts.
9. ✅ **Token usage is collected but never surfaced.** `SessionDigest.tokens_in/out/cache_read`
   are summed per session and then thrown away. Showing them in day notes and project hubs is
   cheap (cost per project per week).
10. ✅ **CI covers Linux + Python 3.11 only**, while the product targets Windows (Task Scheduler,
   `%LOCALAPPDATA%` paths). Add a `windows-latest` job and Python 3.12/3.13 to the matrix.

### Low / maintainability

11. ✅ `config.py` lists every field three times (dataclass, `load_config`, `save_config`).
    Generate load/save from `dataclasses.fields`.
12. ✅ `site.py` (684 lines) embeds HTML, CSS, and JS in f-strings with `{{ }}` escaping. Move
    them to template files.
13. ✅ `DevlogConfig.root_for` silently falls back to `claude_root` for unknown sources. Raise an
    error instead.
14. ✅ The seven slash commands are hand-copied across five assistant folders (`.claude`,
    `.cursor`, `.grok`, `.agents`, `.codex`). Generate them from one source; the drift test
    already exists.
15. Per-project minutes for multi-project days recovered from post text are unknown and shown
    as `—` / `N+`. `devlog obsidian --backfill --rescan` fills them in when the transcripts
    still exist.

## Roadmap

### Phase 2: a deeper graph ✅ shipped

- ✅ **Project identity:** git root + `origin` remote (worktrees resolve to their main repo),
  `home` for sessions started in `~`, and `[project_aliases]` (#6). Project hubs link the
  GitHub repo, a README summary, and open PRs (via `gh`, when installed). Public posts use the
  same identity.
- ✅ **Commits in day notes:** your commits per project that day (filtered by `user.email`),
  linked to GitHub, with counts rolled up to hubs, weeks, and months.
- ✅ **Open threads:** follow-ups from each turn's recap (Claude Code, Codex, Cursor) become
  checkboxes. Ticks persist across regeneration and sync between the day note and the hub.
  Copilot, Grok, and OpenCode parsers capture them too.
- ✅ **Obsidian Bases:** `DevLog/DevLog.base` (Days / Projects / Weeks), embedded on Home.
- ✅ **Monthly and quarterly rollups.**
- ✅ **Token and cost tracking** per project, day, week, and month (#9). Claude Code and Codex
  record the model per turn; cost uses Anthropic's published rates by default, and other models
  are priced only if you add them to `[model_prices]`.

### Phase 3: second-brain features ✅ shipped

- ✅ **Topic notes** (`DevLog/Topics/`): a built-in catalog (genomics and single-cell tools,
  workflow engines, clinical standards, datasets, languages, frameworks) plus your own
  `[topics]` in config. Each topic hub lists days, projects, and topics that often appear
  alongside it. It also has a **Literature & notes** area for Zotero citekeys that is never
  overwritten. Topics are recomputed on every refresh, so a new custom topic applies to all of
  your history after `--reindex`.
- ✅ **Related days:** local TF-IDF similarity in pure Python (no model, no network). Each link
  shows the terms the two days share. Optional local embeddings via Ollama
  (`related_backend = "ollama"`), cached, with TF-IDF as the fallback.
- ✅ **Review and streaks:**
  - Weekly and monthly notes get a *Review* section: time and active days against the previous
    period, new projects, first-time topics, and threads raised.
  - Home shows the current and longest streak, plus a Heatmap Calendar block.
  - Optional retro written by a local model (Ollama, `period_retros = true`), cached until
    the period's facts change.
- ✅ **Per-project JSON Canvas** (`DevLog/Canvas/<project>.canvas`): the hub, then the last 12
  active days, then the top topics. It is regenerated until you rearrange it. After that it's
  yours; delete it to get a fresh one.
- ✅ **Agent memory:** `devlog mcp`, a read-only stdio MCP server built on SDK 2.x, with six
  tools: `list_projects`, `recent_activity`, `project_status`, `open_threads`, `search_log`,
  and `day_log`.

### Phase 4: hardening ✅ shipped

- ✅ **Rollback safety (#4):** publish, delete, and hide share one `undo_local_commit` helper
  that runs `git reset --keep HEAD~1`. Your uncommitted work survives. If a local edit
  overlaps the commit, git refuses and devlog reports it instead of discarding the edit. The
  tests run against real git repositories.
- ✅ **Privacy (#3):** `public_detail = "summary" | "projects" | "verbatim"`, default `projects`.
  - Below `verbatim`, the public post *and* the digest sent to the LLM carry generic work types
    instead of prompts, file names, and commands.
  - `summary` also hides project names.
  - `redact_patterns` adds your own regexes (MRNs, sample IDs), applied everywhere.
  - The vault keeps full detail.
- ✅ **Empty days (#7):** `publish_empty_days = false` by default. Quiet days go to the vault so
  streaks and weekly notes stay accurate, but nothing is committed.
- ✅ **Incremental scans (#5):** parsers take `since` and skip log files whose modification time
  is before the target day (1 h slack). `--rescan` still reads everything.
- ✅ **CI (#10):** Ubuntu and Windows × Python 3.11, 3.12, and 3.13, plus a check that the
  slash commands are in sync.
- ✅ **Maintainability (#11–14):**
  - Config load and save are derived from the dataclass.
  - `root_for` raises on unknown sources.
  - The HTML, CSS, and JS live in `devlog/templates/`, and `site.py` went from 684 to 350
    lines with byte-identical output.
  - The slash commands are generated from `commands/*.md` by `python -m devlog.commands_sync`.

All review findings and roadmap items are now shipped. See **Ideas beyond the roadmap** below.

## Ideas beyond the roadmap

- A post-writing mode on the local model (Ollama) as an alternative to the Claude API path.
- Pull request links (not just commits) in day notes, from `gh`.
- An `--explain` flag for `devlog publish` that shows which settings shaped the post.

## Recommended Obsidian plugins

| Need | Plugin | Notes |
|------|--------|-------|
| Tables over properties | **Bases** (core) or **Dataview** | Home already ships Dataview queries |
| Calendar navigation | **Calendar** + **Periodic Notes** | matches `Daily/` + `Weekly/` naming |
| Open threads | **Tasks** | day notes and hubs emit `- [ ]` open threads |
| Activity heatmap | **Heatmap Calendar** | feed it `active_minutes` |
| Literature linking | **Zotero Integration** | put `[[@citekey]]` links in a topic hub's *Literature & notes* |
| Semantic links | **Smart Connections** (local embeddings) | complements the lexical "related days" |
