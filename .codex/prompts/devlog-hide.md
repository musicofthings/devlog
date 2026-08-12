---
description: Soft-hide a published devlog post from the public feed (markdown kept)
---

Soft-hide a devlog post. If the user typed a date after this command, use it; otherwise ask for the date (format YYYY-MM-DD). First run `devlog hide --date <date> --dry-run` and show the result. Then explicitly ask for confirmation before running the real `devlog hide --date <date>` (no `--dry-run`) -- it excludes the day from the public feed while keeping markdown in `posts/`. Prefer hide over delete when the user wants reversible visibility. Never run the real hide without an explicit yes from the user.
