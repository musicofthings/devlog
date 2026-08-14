---
name: devlog-obsidian
description: Mirror published posts into a local Obsidian vault. Use when the user asks to backfill, sync, or write devlog notes to Obsidian or run /devlog-obsidian.
---

Run `devlog obsidian` in this repository. If the user wants every existing post mirrored, run `devlog obsidian --backfill`. If they typed a date (YYYY-MM-DD, "today", or "yesterday"), pass it as `--date <that value>` (with or without `--backfill`). Prefer `--dry-run` first and show the intended vault paths, then run without `--dry-run` after they confirm. Show the full output. If config has no `obsidian_vault`, tell them to set it with `devlog init` (blank vault path means the Obsidian mirror is disabled).
