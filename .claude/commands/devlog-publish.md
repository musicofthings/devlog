---
description: Publish today's or a specific day's devlog post
argument-hint: "[YYYY-MM-DD | today | yesterday]"
---

Run `devlog publish` in this repository. If an argument was given ($ARGUMENTS), pass it as `--date $ARGUMENTS`; otherwise let devlog use its own default (yesterday). Show the full output. If it reports the post was "skipped" because it already exists, ask before rerunning with `--force`. If publish_mode is `review` (or the output says `pending_review`), remind the user they can push after editing with `devlog publish --confirm --date <date>`.
