# Anthropic Workbench paste kit (token-efficient)

Model: `claude-sonnet-4-6` (or closest Sonnet)
Temperature: 0–0.3
Max tokens: 180

Full digest chars: 1180 · Compact digest chars: 749 (63% of full)

## System

```
Write a first-person daily build-log post from the digest only. Plain language, 3-5 short sentences, no hype/emojis/exclamation points. Name specific projects and changes. Invent nothing.
```

## User

```
Digest:
394 min, 3 session(s): patent, span, variantgpt

[variantgpt, 5m]
  Tasks: Refactor the VCF parser so it streams instead of loading the whole file into memory | Great, now add a progress bar for large files
  Tools: Read x1, Edit x2, Bash x1
  Files: vcf_parser.py
  Cmds: pytest tests/test_vcf_parser.py -q

[patent, 29m]
  Tasks: Draft the claims section for the orbital thermal dissipation patent | Search for prior art on radiative cooling panels in orbit
  Tools: Write x1, WebSearch x1
  Files: claims_draft_v3.md

[span, 360m]
  Tasks: Investigate the flaky nightly ingest job | Kick off the job again and watch it cross midnight
  Tools: Bash x2, Edit x1
  Files: scheduler.py
  Cmds: tail -n 200 logs/ingest.log, python -m ingest.run --once

Post:
```
