---
description: Build a slide outline of a week, month, quarter, or project from the devlog vault
argument-hint: "[week|month|quarter|project <name>] [summary|projects|verbatim]"
---

Build a slide outline from the devlog vault for $ARGUMENTS. Map the request to flags: a week (`--week` or `--week 2026-W39`), month (`--month` / `--month 2026-09`), quarter (`--quarter`), a date range (`--since … --until …`), and/or `--project <name>`. Ask which detail level to use if the user didn't say: `summary` (totals only), `projects` (names, counts, pipelines; the default from config), or `verbatim` (prompts, commit/PR titles, open threads, papers) — remind them verbatim is for private audiences. Run `devlog deck <flags> --detail <level> --out devlog-deck.md`, show the outline, and offer edits. If a Gamma or other presentation tool is available, offer to create the presentation from the outline (one card per `---` section); otherwise tell the user to paste it into Gamma via Create → Paste in text → card-by-card.
