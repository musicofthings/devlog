---
description: Soft-hide a published devlog post from the public feed (markdown kept)
skill-description: Soft-hide a published devlog post from the public feed. Use when the user asks to hide a daily-dev-log post or run /devlog-hide.
argument-hint: "<YYYY-MM-DD>"
---

<!-- claude -->
Soft-hide a devlog post for $ARGUMENTS (ask for the date if not given, format YYYY-MM-DD). First run `devlog hide --date <date> --dry-run` and show the result. Then ask for confirmation before running `devlog hide --date <date>` (no `--dry-run`). Hide removes the day from the public feed and day HTML but keeps `posts/YYYY-MM-DD.md` in the repo. Prefer hide over delete when the user wants to reverse visibility without a hard git removal. Never run the real hide without an explicit yes from the user.

<!-- skills -->
Soft-hide a devlog post. If the user typed a date after this command, use it; otherwise ask for the date (format YYYY-MM-DD). First run `devlog hide --date <date> --dry-run` and show the result. Then explicitly ask for confirmation before running the real `devlog hide --date <date>` (no `--dry-run`) -- it excludes the day from the public feed while keeping markdown in `posts/`. Prefer hide over delete when the user wants reversible visibility. Never run the real hide without an explicit yes from the user.
