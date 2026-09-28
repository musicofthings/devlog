---
description: Check devlog's publish/delete status and scheduled-task health
---

Give a quick devlog status check:
1. Run `devlog doctor` and report any FAIL lines with their fix, then warnings briefly (it checks config, log sources, the site repo and its last publish, an audit of published posts, GitHub CLI, the vault, Ollama/Zotero if configured, and the scheduled task).
2. Read `.devlog-status.json` at the repo root if present, and report the last published/deleted/hidden date and time.
3. If `.devlog-hidden.json` exists, list currently soft-hidden dates.
4. Run `devlog publish --dry-run` to preview what the next publish would contain, without writing or pushing anything.
5. Report doctor's `schedule` line (the nightly job: Task Scheduler task `DailyDevLogPublish` on Windows, launchd agent `dev.devlog.publish` on macOS, cron on Linux). If it's missing or broken, say so and offer to run `devlog init --schedule-only`, which re-registers it from the current config (on Linux it prints the cron line to add).

Summarize all of this concisely.
