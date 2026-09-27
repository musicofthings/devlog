---
name: devlog-obsidian
description: Mirror published posts into a local Obsidian vault. Use when the user asks to backfill, sync, or write devlog notes to Obsidian or run /devlog-obsidian.
---

Run `devlog obsidian` in this repository. If the user wants every existing post mirrored, run `devlog obsidian --backfill`. If they typed a date (YYYY-MM-DD, "today", or "yesterday"), pass it as `--date <that value>` (with or without `--backfill`). Prefer `--dry-run` first and show the intended vault paths, then run without `--dry-run` after they confirm. Show the full output. If config has no `obsidian_vault`, tell them to set it with `devlog init` (blank vault path means the Obsidian mirror is disabled).

Mirroring also links the vault as a knowledge graph: project hubs (`DevLog/Projects/`), work-type hubs (`DevLog/Work/`), weekly rollups (`DevLog/Weekly/`), and a `DevLog/DevLog Home.md` dashboard. For richer per-project links on old posts, add `--rescan` to `--backfill` (re-reads local session logs; slower). To regenerate hubs only, run `devlog obsidian --reindex`. Notes the user writes below `%% devlog:end %%` in any generated note are preserved.

Phase 3 adds topic hubs (`DevLog/Topics/`, custom topics via `[topics]` in config; apply to history with `--reindex`), related days, weekly/monthly reviews, streaks, and per-project canvases (`DevLog/Canvas/`). To let agents query this memory, register `devlog mcp` as an MCP server (needs `pip install -e ".[mcp]"`).
