# Daily Dev Log — Phase 2

Turns local AI coding session history into one short, factual, first-person
"build log" post per day. Phase 2 ships Claude Code, Codex, and Cursor
(agent-transcript) source plugins; a default run merges all three into one post.

**Repo:** https://github.com/musicofthings/devlog

## Results (verified locally)

| Suite | Result |
|-------|--------|
| `pytest` | **70 passed** |
| Offline evals (`python -m evals.run`) | **8/8 passed** |
| Live evals (`python -m evals.run --live`) | **8/8 passed** |

Token-efficient Claude path: compact, redacted digests with `src=` tags,
empty-day API skip, explicit external-API consent, and a 5-sentence output clamp.

## Layout

```
devlog/
  cli.py              # argparse entrypoint (per-source roots)
  digest.py           # calendar-day slicing (local timezone) + compact digests
  models.py           # RawSession, SessionDigest
  summarize.py        # digest → post (Claude API or template fallback)
  sources/
    base.py           # plugin registry
    claude_code.py    # ~/.claude projects JSONL
    codex.py          # ~/.codex sessions/YYYY/MM/DD/rollout-*.jsonl
    cursor.py         # ~/.cursor/projects/*/agent-transcripts/**/*.jsonl
evals/                # acceptance-style offline/live eval harness
main.py               # thin wrapper → devlog.cli
sample_data/          # claude_code, codex, cursor fixtures
tests/                # pytest suite
docs/                 # GitHub Pages landing + superpowers specs/plans
```

## Setup

```bash
pip install -e ".[dev]"
```

## Run

Print today's post without writing a file (defaults: all three sources):

```bash
python main.py --date today --dry-run
```

Against bundled sample data:

```bash
python main.py --date 2026-07-22 --sources claude_code --claude-root sample_data/claude_code
python main.py --date 2026-07-20 --sources codex,cursor \
  --codex-root sample_data/codex --cursor-root sample_data/cursor --dry-run
```

Against real local logs:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # optional
python main.py --date today --dry-run --verbose --allow-external-api
```

External API use is disabled by default even when a key is present. Enable it
with `--allow-external-api` for a run or set `allow_external_api = true` in the
config file. Transcript-derived text is redacted before it leaves the local
pipeline. Writes `devlog-YYYY-MM-DD.md` unless `--dry-run` is set, and refuses
to replace an existing post unless `--force` is supplied.

### CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--date` | `today` | Target day (`YYYY-MM-DD` or `today`, local timezone) |
| `--sources` | `claude_code,codex,cursor` | Comma-separated source plugins |
| `--claude-root` | `~/.claude` | Claude Code data root |
| `--codex-root` | `~/.codex` | Codex data root |
| `--cursor-root` | `~/.cursor` | Cursor data root (agent transcripts) |
| `--sample-mode` | off | Optional/legacy; Claude sample layout is auto-detected |
| `--dry-run` | off | Print post; do not write `devlog-*.md` |
| `--force` | off | Replace an existing generated post |
| `--allow-external-api` | off | Permit redacted transcript text to be sent to the model API |
| `--verbose` | off | Extra diagnostics per source |

Missing roots are skipped (other sources still run).

## Tests

```bash
python -m pytest
# expected: 70 passed
```

## Evals

```bash
python -m evals.run          # expected: 8/8
python -m evals.run --json
python -m evals.run --live   # requires ANTHROPIC_API_KEY
```

Also covered by pytest via `tests/test_evals.py`.

## Publish (Phase 3)

Initialize config (writes `%USERPROFILE%\.config\devlog\config.toml`):

```bash
devlog init --defaults          # non-interactive
devlog init                     # prompts; can register Task Scheduler
```

Publish yesterday's post into `posts/` + rebuild `docs/log/`:

```bash
devlog publish --dry-run
devlog publish                  # uses publish_mode from config: auto | pr | manual
devlog publish --date 2026-07-20 --force
```

Enable GitHub Pages: repo **Settings → Pages → Source: GitHub Actions**
(workflow: `.github/workflows/pages.yml` uploads `docs/` as the site root).

Public URLs after deploy:

- Landing: https://musicofthings.github.io/devlog/
- Log feed: https://musicofthings.github.io/devlog/log/
- Day post: https://musicofthings.github.io/devlog/log/YYYY-MM-DD.html

## Manual acceptance

1. `python main.py --date 2026-07-20 --dry-run --verbose` (or another day with real activity).
2. Confirm Codex paths use real `cwd` and Cursor paths decode from `projects/` folder names.
3. Confirm a multi-tool day produces one merged post with distinct sources in digests (`--verbose`).
4. `devlog init --defaults` then `devlog publish --date <fixture-day> --dry-run`.

## Next

- Recruiter-facing portfolio polish and custom domain.
- Optional Cursor SQLite / Composer history (deferred; agent transcripts only for now).

See `PRD_TRD.md` and `docs/superpowers/` for full spec and design.
