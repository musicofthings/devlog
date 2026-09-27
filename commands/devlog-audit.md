---
description: Scan published devlog posts for leaked harness text, secrets, identifiers, and paths
skill-description: Audit published daily-dev-log posts for privacy leaks (harness text, secrets, MRN-style identifiers, user paths) and optionally rewrite them. Use when the user asks to audit, scrub, or check the public devlog, or runs /devlog-audit.
argument-hint: "[YYYY-MM-DD]"
---

<!-- claude -->
Audit the published devlog for privacy leaks. Run `devlog audit` (add `--date $ARGUMENTS` if a date was given) and show the findings: LEAK lines (harness text, secrets, identifiers from `redact_patterns`/`redact_presets`, user paths) fail the audit; note lines (local folder paths, login name, continuation nudges) do not. If there are findings, offer `devlog audit --fix --no-commit` so the user can review the rewritten posts with `git diff`, then `devlog audit --fix` to commit and push. Never run `--fix` without `--no-commit` unless the user explicitly says yes. Mention that git history keeps the old text.

<!-- skills -->
Audit the published devlog for privacy leaks. If the user typed a date after this command, pass it as `--date`. Run `devlog audit` and show the findings: LEAK lines (harness text, secrets, identifiers from `redact_patterns`/`redact_presets`, user paths) fail the audit; note lines (local folder paths, login name, continuation nudges) do not. If there are findings, offer `devlog audit --fix --no-commit` so the user can review the rewritten posts with `git diff`, then `devlog audit --fix` to commit and push. Never run `--fix` without `--no-commit` unless the user explicitly says yes. Mention that git history keeps the old text.
