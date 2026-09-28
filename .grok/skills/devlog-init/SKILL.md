---
name: devlog-init
description: Set up or update devlog configuration (sources, publish mode, schedule). Use when the user asks to init or configure daily-dev-log / run /devlog-init.
user-invocable: true
---

Run `devlog init` in this repository to set up or update its configuration -- it prompts for source roots, publish mode, an Obsidian vault (auto-detected from Obsidian, or it will create ~/Documents/DevLog; answering `-` skips the local mirror), and then offers the nightly job for this platform: a Task Scheduler task on Windows, a launchd agent on macOS; on Linux it prints a cron line to add with `crontab -e`. It also writes a publish-now shortcut to the Desktop (`.cmd` on Windows, `.command` on macOS). To (re)register only the nightly job without re-answering the prompts, run `devlog init --schedule-only`. Show the output and summarize what got configured, especially `publish_mode` (since `auto` means posts publish with no review, `manual`/`pr` require a human step), `public_detail` (`summary` | `projects` | `verbatim` -- only `verbatim` puts prompt text in public posts), and `obsidian_vault`. Mention that `redact_patterns` (regexes for MRNs, sample IDs, etc.), `project_aliases`, and `topics` are edited in the config file and survive re-running init.
