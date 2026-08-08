---
name: devlog-publish
description: Publish today's or a specific day's devlog post. Use when the user asks to publish a daily-dev-log post or run /devlog-publish.
---

Run `devlog publish` in this repository. If the user typed a date (YYYY-MM-DD, "today", or "yesterday") after this command, pass it as `--date <that value>`; otherwise let devlog use its own default (yesterday). Show the full output. If it reports the post was "skipped" because it already exists, ask before rerunning with `--force`.
