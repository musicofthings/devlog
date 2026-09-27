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
3. **Verbatim prompts go to a public site.** The template post quotes your first prompt per
   project word for word (e.g. "help me clean up disk space…", "remove all mcp servers…").
   In a clinical or research setting a prompt can contain patient identifiers, sample IDs, or
   unpublished results. `privacy.py` only redacts API keys and the home path. Recommendations:
   - Add a `public_detail = "summary" | "projects" | "verbatim"` setting. The public post gets
     the reduced form; the vault always keeps full detail.
   - Add a user regex denylist (MRNs, sample-ID patterns, client names).
   - Default nightly jobs to `review` mode for anyone working with clinical data.
4. **`git reset --hard HEAD~1` in rollback can destroy unrelated work.**
   `publish._restore_failed_publish` and `delete_cmd._restore_failed_delete` hard-reset the
   whole checkout. `_ensure_managed_paths_clean` only checks `posts/`, `docs/log/` etc., so
   uncommitted edits elsewhere in the repo (for example, devlog code you're working on) are
   wiped when a nightly push fails. Use `git reset --keep HEAD~1`, which aborts instead of
   clobbering, or a soft reset plus a checkout of only the managed paths. Update the matching
   assertions in `tests/test_publish.py` and `tests/test_delete.py`.

### Medium

5. **Every run re-parses your entire history.** Each `iter_sessions` walks and parses every
   transcript ever written (e.g. `sources/claude_code.py:212`). The cost grows without bound.
   Pass a `since` date and skip files whose mtime is older than the target day minus 1.
   `obsidian --rescan` already batches this into one pass.
6. **Project identity is `basename(cwd)`.** That yields `shibi` (your home directory),
   `window`, and `delete-post` (a worktree or branch folder) as "projects", and it split
   `Gurukul` from `gurukul`. Case is now merged in the vault. Next steps: resolve the cwd to the
   git top-level folder and the `origin` repo name, and add a `[project_aliases]` table in
   `config.toml`.
7. **Empty days clutter the feed and history.** 20 of 44 posts say "No coding activity
   logged today." Add `publish_empty_days = false`. The vault already treats these as "quiet
   days" and navigation skips them.
8. **Template posts are low-signal** ("Tools: Read (104x), StrReplace (92x)"), and the
   LLM path is off by default for privacy. Options:
   - A local model backend (Ollama or llama.cpp) so summarization never leaves the machine.
   - A richer template: say what changed (files and commits), not how many tool calls ran.
9. **Token usage is collected but never surfaced.** `SessionDigest.tokens_in/out/cache_read`
   are summed per session and then thrown away. Showing them in day notes and project hubs is
   cheap (cost per project per week).
10. **CI covers Linux + Python 3.11 only**, while the product targets Windows (Task Scheduler,
   `%LOCALAPPDATA%` paths). Add a `windows-latest` job and Python 3.12/3.13 to the matrix.

### Low / maintainability

11. `config.py` lists every field three times (dataclass, `load_config`, `save_config`).
    Generate load/save from `dataclasses.fields`.
12. `site.py` (684 lines) embeds HTML, CSS, and JS in f-strings with `{{ }}` escaping. Move
    them to template files.
13. `DevlogConfig.root_for` silently falls back to `claude_root` for unknown sources. Raise an
    error instead.
14. The seven slash commands are hand-copied across five assistant folders (`.claude`,
    `.cursor`, `.grok`, `.agents`, `.codex`). Generate them from one source; the drift test
    already exists.
15. Per-project minutes for multi-project days recovered from post text are unknown and shown
    as `—` / `N+`. `devlog obsidian --backfill --rescan` fills them in when the transcripts
    still exist.

## Roadmap

### Phase 2: a deeper graph (high value, low risk)

- **Project identity:** git top-level + remote detection and `[project_aliases]` (#6). Project
  hubs then link to the GitHub repo, its README summary, and open PRs.
- **Commits in day notes:** run `git log --since/--until` for each project touched that day,
  and list commits with links. This turns "asked for X" into "shipped Y".
- **Open threads:** pull "next steps" / TODOs from the assistant's final message in each
  session into `- [ ]` tasks. Each project hub gets an *Open threads* section (Tasks-plugin
  compatible).
- **Obsidian Bases:** generate `DevLog/DevLog.base` with native table views of days and
  projects. This needs no community plugin; Dataview stays optional.
- **Monthly and quarterly rollups**, compatible with Periodic Notes.
- **Token and cost tracking** per project (#9).

### Phase 3: second-brain features

- **Topic/entity notes:** detect libraries, tools, and datasets mentioned in prompts and files
  (scanpy, Nextflow, GATK, VCF, CELLxGENE…) and link days to topic notes. Those topic notes
  can link to literature notes (Zotero Integration plugin), which connects your build log to
  your reading.
- **"Related days" links** from local embeddings (sentence-transformers or Ollama
  embeddings). Similar work gets linked across projects with no network calls.
- **Weekly review note:** optional LLM retro (local model by default), streaks, and a
  heatmap data file for the Heatmap Calendar plugin.
- **Per-project JSON Canvas** timelines (`.canvas`).
- **Agent-queryable memory:** a small MCP server over `DevLog/.devlog/index.json`. Claude Code,
  Codex, and others could then answer "what did I do on vitreous last week, and what's still
  open?" at session start.

### Phase 4: hardening

Items 3, 4, 5, 7, 10–14 above. Privacy (3) and rollback safety (4) come first.

## Recommended Obsidian plugins

| Need | Plugin | Notes |
|------|--------|-------|
| Tables over properties | **Bases** (core) or **Dataview** | Home already ships Dataview queries |
| Calendar navigation | **Calendar** + **Periodic Notes** | matches `Daily/` + `Weekly/` naming |
| Open threads | **Tasks** | once Phase 2 emits `- [ ]` items |
| Activity heatmap | **Heatmap Calendar** | feed it `active_minutes` |
| Literature linking | **Zotero Integration** | topic notes ↔ papers (Phase 3) |
| Semantic links | **Smart Connections** (local embeddings) | complements Phase 3 "related days" |
