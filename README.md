# Daily Dev Log — Phase 1

Turns local AI coding session history into one short, factual, first-person
"build log" post per day. Phase 1 ships a Claude Code source plugin; Codex and
Cursor are registered stubs for phase 2.

**Repo:** https://github.com/musicofthings/devlog

## Results (verified locally)

| Suite | Result |
|-------|--------|
| `pytest` | **27 passed** |
| Offline evals (`python -m evals.run`) | **5/5 passed** |
| Live evals (`python -m evals.run --live`) | **5/5 passed** (compact digest + token caps) |

Token-efficient Claude path: compact digests (~37% shorter on sample day),
`max_tokens=120`, empty-day API skip, 5-sentence output clamp.

## Layout

```
devlog/
  cli.py              # argparse entrypoint
  digest.py           # calendar-day slicing (local timezone) + compact digests
  models.py           # RawSession, SessionDigest
  summarize.py        # digest → post (Claude API or template fallback)
  sources/
    base.py           # plugin registry
    claude_code.py    # JSONL parser (FR1–FR4)
    codex.py          # stub
    cursor.py         # stub
evals/                # acceptance-style offline/live eval harness
main.py               # thin wrapper → devlog.cli
sample_data/claude_code/   # synthetic JSONL for offline tests
tests/                # pytest suite
docs/                 # GitHub Pages landing
```

## Setup

```bash
pip install -e ".[dev]"
```

## Run

Print today's post without writing a file:

```bash
python main.py --date today --dry-run
```

Run against bundled sample data (layout is auto-detected; `--sample-mode` is optional):

```bash
python main.py --date 2026-07-22 --claude-root sample_data/claude_code
```

Against real Claude Code logs (default root `~/.claude`):

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # optional; template fallback without it
python main.py --date today
```

Writes `devlog-YYYY-MM-DD.md` unless `--dry-run` is set.

### CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--date` | `today` | Target day (`YYYY-MM-DD` or `today`, local timezone) |
| `--sources` | `claude_code` | Comma-separated source plugins |
| `--claude-root` | `~/.claude` | Root dir for session data |
| `--sample-mode` | off | Optional/legacy; sample layout is auto-detected by the Claude Code parser |
| `--dry-run` | off | Print post; do not write `devlog-*.md` |
| `--verbose` | off | Extra diagnostics per source |

## Tests

```bash
python -m pytest
# expected: 27 passed
```

## Evals

Phase 1 acceptance-style evals against `sample_data/` (offline template by default):

```bash
python -m evals.run          # expected: 5/5
python -m evals.run --json
python -m evals.run --live   # requires ANTHROPIC_API_KEY; expected: 5/5
```

Also covered by pytest via `tests/test_evals.py`.

## Manual acceptance (real ~/.claude)

Before relying on output in production:

1. Run against real `~/.claude` for at least one day with `--dry-run`.
2. Confirm project paths look correct (not garbled dashes from lossy folder decode).
3. Confirm `--date today` matches your local calendar day.

## Next

- Flesh out Codex and Cursor source plugins (phase 2).
- Publishing integrations beyond the local markdown file.

See `PRD_TRD.md` and `docs/superpowers/` for full spec and design.
