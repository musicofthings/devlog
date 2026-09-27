# Changelog

All notable changes to devlog (`daily-devlog` on PyPI). The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.0] — Unreleased

The vault became a linked second brain, publishing got privacy controls and
safer automation, and devlog learned about pipelines, papers, and slides.

### Added
- **Obsidian knowledge graph.** Day notes linked to project, work-type, and
  topic hubs; weekly, monthly, and quarterly rollups; a Home dashboard with
  streaks and a heatmap; Bases views; per-project JSON Canvas; related days
  (TF-IDF, or local Ollama embeddings); period reviews and optional local retros.
- **What happened, not just how long.** Your commits and pull requests per
  project per day, open threads from agent recaps (tick them off in Obsidian),
  token and API-equivalent cost tracking, README summaries and open PRs on hubs.
- **Pipeline runs.** Nextflow (`.nextflow/history`, `.nextflow.log`) and
  Snakemake (`.snakemake/log`) runs in day notes, project hubs, and
  `DevLog/Pipelines/` hubs; failed runs become open threads; `pipeline_dirs`
  for runs launched outside project folders. Edited notebooks are linked.
- **Literature.** DOIs, PMIDs, PMC IDs, and arXiv IDs you mention are linked in
  the vault and matched to Zotero citekeys through Better BibTeX (`[[@citekey]]`),
  with a `DevLog/Literature.md` index.
- **`devlog deck`.** Slide outlines of a week, month, quarter, or project for
  Gamma, Marp, or PowerPoint, gated by `--detail`; `/devlog-deck`.
- **`devlog audit`.** Scans published posts for harness text, secrets,
  identifiers, and user paths; `--fix` rewrites them. Runs in CI.
- **Privacy controls.** `public_detail` (summary / projects / verbatim),
  `redact_patterns`, `redact_presets` (`mrn`, `dob`, `ssn`, `phone`, `email`,
  `clinical`), `publish_empty_days`.
- **Local post writer.** `post_writer = "ollama"` writes the public post with a
  local model; `devlog publish --explain` shows how a post was put together.
- **Agent memory.** `devlog mcp` (read-only by default) and opt-in write tools
  with `mcp_write = true`: `close_thread`, `add_note`, `log_decision`, all
  logged to `.devlog/agent-writes.jsonl`.
- **`devlog doctor`** checks the whole setup and says how to fix each problem;
  `devlog --version`.
- **Site.** Search box on the log page (with `?q=` links) and an RSS feed at
  `log/feed.xml`.
- **Project identity** from the git root and `origin` remote (worktrees resolve
  to their repo, `~` becomes `home`), plus `project_aliases`.
- Slash commands generated for Claude Code, Cursor, Grok, and Codex from
  `commands/*.md`.

### Changed
- Licensed under MIT (see `LICENSE`).
- Package renamed to `daily-devlog` for PyPI (the command is still `devlog`);
  the repo-only `evals` package and `devlog-evals` script are no longer installed.
- Parsers read only log files modified since the target day (incremental scans).
- The site's HTML, CSS, and JS live in `devlog/templates/`.
- Config load/save derive from the dataclass; re-running `devlog init` keeps
  hand-edited settings.
- CI: Ubuntu and Windows × Python 3.11–3.13, ruff, mypy, slash-command sync,
  post audit, and a package build with `twine check`.

### Fixed
- Harness-injected text (MCP manifests, AGENTS.md, skill preambles) no longer
  becomes a post's "task"; the ten affected published posts were rewritten.
- The Daily Note embed was hidden inside an Obsidian comment and never rendered.
- Rollback after a failed push used `git reset --hard` and could discard
  unrelated work; it now uses `git reset --keep`.
- Dark theme: the landing page's secondary button text was invisible.

## 0.1.0

- The original, untagged version: nightly public build-log posts from Claude Code, Codex,
  Cursor, Grok, Copilot CLI, OpenCode, Warp, Vitreous, and Antigravity logs;
  GitHub Pages feed; hide/delete from the site; Obsidian mirror; Windows
  scheduled task.

[0.2.0]: https://github.com/musicofthings/devlog/releases/tag/v0.2.0
