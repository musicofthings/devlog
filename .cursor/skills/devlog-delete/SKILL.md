---
name: devlog-delete
description: Delete a previously published devlog post (with confirmation)
---

Delete a devlog post. If the user typed a date after this command, use it; otherwise ask for the date (format YYYY-MM-DD). First run `devlog delete --date <date> --dry-run` and show the result so the post's existence is confirmed. Then explicitly ask for confirmation before running the real `devlog delete --date <date>` (no `--dry-run`) -- it's a real commit that removes the post from the live site, recoverable only via git history, not the live site. Never run the real delete without an explicit yes from the user.
