---
name: devlog-publish
description: Publish today's or a specific day's devlog post
argument-hint: "[YYYY-MM-DD | today | yesterday]"
user-invocable: true
---

Run `devlog publish` in this repository. If the user gave an argument (a date, "today", or "yesterday"), pass it as `--date <that value>`; otherwise let devlog use its own default (yesterday). Show the full output. If it reports the post was "skipped" because it already exists, ask before rerunning with `--force`. If publish_mode is `review` (or the output says `pending_review`), remind the user they can push after editing with `devlog publish --confirm --date <date>`.
