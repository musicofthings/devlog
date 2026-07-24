# Daily Dev Log — Phase 1

Turns local AI coding session history into one short, factual, first-person
"build log" post per day. Phase 1 ships a Claude Code source plugin; Codex and
Cursor are registered stubs for phase 2.

## Layout

```
devlog/
  cli.py              # argparse entrypoint
  digest.py           # calendar-day slicing (local timezone)
  models.py           # RawSession, SessionDigest
  summarize.py        # digest → post (Claude API or template fallback)
  sources/
    base.py           # plugin registry
    claude_code.py    # JSONL parser (FR1–FR4)
    codex.py          # stub
    cursor.py         # stub
main.py               # thin wrapper → devlog.cli
sample_data/claude_code/   # synthetic JSONL for offline tests
tests/                # pytest suite
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

Run against bundled sample data:

```bash
python main.py --date 2026-07-22 --claude-root sample_data/claude_code --sample-mode
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
| `--sample-mode` | off | Use bundled `sample_data/claude_code` layout |
| `--dry-run` | off | Print post; do not write `devlog-*.md` |
| `--verbose` | off | Extra diagnostics per source |

## Tests

```bash
python -m pytest
```

## Manual acceptance (real ~/.claude)

Before relying on output in production:

1. Run against real `~/.claude` for at least one day with `--dry-run`.
2. Confirm project paths look correct (not garbled dashes from lossy folder decode).
3. Confirm `--date today` matches your local calendar day.

## Next

- Flesh out Codex and Cursor source plugins (phase 2).
- GitHub repo landing page and publish step (deferred).

See `PRD_TRD.md` and `docs/superpowers/` for full spec and design.
