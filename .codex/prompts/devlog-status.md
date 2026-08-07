---
description: Check devlog's publish/delete status and scheduled-task health
---

Give a quick devlog status check:
1. Read `.devlog-status.json` at the repo root if present, and report the last published/deleted date and time.
2. Run `devlog publish --dry-run` to preview what the next publish would contain, without writing or pushing anything.
3. On Windows, check whether the nightly scheduled task is registered: `schtasks /Query /TN DailyDevLogPublish /V /FO LIST`. If it's missing, say so and offer to run `devlog init` to re-register it.

Summarize all of this concisely.
