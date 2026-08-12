---
name: devlog-unhide
description: Unhide a soft-hidden devlog post back onto the public feed. Use when the user asks to unhide a daily-dev-log post or run /devlog-unhide.
---

Unhide a soft-hidden devlog post. If the user typed a date after this command, use it; otherwise ask for the date (format YYYY-MM-DD). First run `devlog unhide --date <date> --dry-run` and show the result. Then explicitly ask for confirmation before running the real `devlog unhide --date <date>` (no `--dry-run`). Never run the real unhide without an explicit yes from the user.
