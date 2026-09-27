---
name: devlog-deck
description: Turn the daily-dev-log vault into a slide outline (Markdown, --- between slides) for Gamma, Marp, or PowerPoint. Use when the user asks for a weekly/monthly/quarterly review deck, a project summary deck, or runs /devlog-deck.
---

Build a slide outline from the devlog vault. If the user typed a period or project after this command, use it. Map the request to flags: a week (`--week` or `--week 2026-W39`), month (`--month` / `--month 2026-09`), quarter (`--quarter`), a date range (`--since … --until …`), and/or `--project <name>`. Ask which detail level to use if the user didn't say: `summary` (totals only), `projects` (names, counts, pipelines; the default from config), or `verbatim` (prompts, commit/PR titles, open threads, papers) — remind them verbatim is for private audiences. Run `devlog deck <flags> --detail <level> --out devlog-deck.md`, show the outline, and offer edits. If a presentation tool is available, offer to create the presentation from the outline (one card per `---` section); otherwise tell the user to paste it into Gamma via Create → Paste in text → card-by-card.
