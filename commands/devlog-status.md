---
description: Check devlog's publish/delete status and scheduled-task health
skill-description: Check devlog publish/delete status and scheduled-task health. Use when the user asks for daily-dev-log status or run /devlog-status.
---

Give a quick devlog status check:
1. Run `devlog doctor` and report any FAIL lines with their fix, then warnings briefly (it checks config, log sources, the site repo and its last publish, an audit of published posts, GitHub CLI, the vault, Ollama/Zotero if configured, and the scheduled task).
2. Read `.devlog-status.json` at the repo root if present, and report the last published/deleted/hidden date and time.
3. If `.devlog-hidden.json` exists, list currently soft-hidden dates.
4. Run `devlog publish --dry-run` to preview what the next publish would contain, without writing or pushing anything.
5. On Windows, if doctor didn't already cover it, check whether the nightly scheduled task is registered: `schtasks /Query /TN DailyDevLogPublish /V /FO LIST`. If it's missing, say so and offer to run `devlog init` to re-register it.

Summarize all of this concisely.
