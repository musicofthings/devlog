---
name: devlog-init
description: Set up or update devlog configuration (sources, publish mode, schedule)
---

Run `devlog init` in this repository to set up or update its configuration -- it prompts for source roots, publish mode, an Obsidian vault (auto-detected from Obsidian, or it will create ~/Documents/DevLog; blank skips the local mirror), and optionally registers the nightly scheduled task. Show the output and summarize what got configured, especially `publish_mode` (since `auto` means posts publish with no review, `manual`/`pr` require a human step) and `obsidian_vault`.
