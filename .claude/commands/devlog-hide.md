---
description: Soft-hide a published devlog post from the public feed (markdown kept)
argument-hint: "<YYYY-MM-DD>"
---

Soft-hide a devlog post for $ARGUMENTS (ask for the date if not given, format YYYY-MM-DD). First run `devlog hide --date <date> --dry-run` and show the result. Then ask for confirmation before running `devlog hide --date <date>` (no `--dry-run`). Hide removes the day from the public feed and day HTML but keeps `posts/YYYY-MM-DD.md` in the repo. Prefer hide over delete when the user wants to reverse visibility without a hard git removal. Never run the real hide without an explicit yes from the user.
