---
name: devlog-delete
description: Delete a previously published devlog post (with confirmation)
argument-hint: "<YYYY-MM-DD>"
user-invocable: true
---

Delete a devlog post for the given date (ask for it if not given, format YYYY-MM-DD). First run `devlog delete --date <date> --dry-run` and show the result so the post's existence is confirmed. Then explicitly ask for confirmation before running the real `devlog delete --date <date>` (no `--dry-run`) -- it's a real commit that removes the post from the live site, recoverable only via git history, not the live site. Never run the real delete without an explicit yes from the user. Vault notes are preserved by default; only add `--also-obsidian` if the user explicitly asks to remove the Obsidian archive and Daily Note embed too.
