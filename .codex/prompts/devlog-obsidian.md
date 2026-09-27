---
description: "DEPRECATED — use Codex skill .agents/skills/devlog-obsidian instead"
---

Custom Codex prompts (`/prompts:…`) were removed in Codex CLI 0.117+.
Use the repo skill at `.agents/skills/devlog-obsidian/SKILL.md` (or ask Codex
to run the `devlog-obsidian` skill).

Mirroring also links the vault as a knowledge graph: project hubs (`DevLog/Projects/`), work-type hubs (`DevLog/Work/`), weekly rollups (`DevLog/Weekly/`), and a `DevLog/DevLog Home.md` dashboard. For richer per-project links on old posts, add `--rescan` to `--backfill` (re-reads local session logs; slower). To regenerate hubs only, run `devlog obsidian --reindex`. Notes the user writes below `%% devlog:end %%` in any generated note are preserved.
