# Daily Dev Log — Phase 1 (Claude Code)

Turns your local Claude Code session history into one short, factual,
first-person "build log" post per day. Built and tested here against
synthetic session data; ready for you to point at your real
`~/.claude/projects/` and see what it produces on an actual day.

## Files

- `claude_code_parser.py` — reads Claude Code's local JSONL transcripts
  and extracts, per session: project, start/end time, your own task
  messages, tool calls (Edit/Read/Bash/etc.), files touched, and token
  usage.
- `summarize.py` — condenses a day's sessions into a factual digest, then
  either (a) asks Claude to narrate it into a short post, or (b) if no
  API key is available, falls back to a deterministic template so the
  pipeline still runs end-to-end.
- `main.py` — CLI entrypoint.
- `sample_data/` — two synthetic sessions (different projects, same day)
  used to test the parser without needing real logs.

## Run it on your own machine

1. Copy these files anywhere, e.g. `~/devlog/`.
2. Make sure you actually have Claude Code sessions from today:
   `ls ~/.claude/projects/`
3. (Optional but recommended) set your API key so you get a real
   narrated post instead of the template fallback:
   `export ANTHROPIC_API_KEY=sk-ant-...`
4. Run:
   ```
   cd ~/devlog
   python3 main.py --date today
   ```
   This scans `~/.claude/projects/` for every session touched today,
   builds the digest, and writes `devlog-YYYY-MM-DD.md`.

To re-run against the bundled synthetic test data instead of your real
logs:
```
python3 main.py --date 2026-07-22 --claude-root sample_data --sample-mode
```

## What I verified in this test run

- Parser correctly reconstructs two separate sessions across two
  different projects on the same day, with the right task text, tool
  call counts, files touched, and token counts pulled straight from the
  JSONL fields.
- The digest → post pipeline runs end-to-end without an API key (template
  fallback), and is wired to call Claude for a proper narrated post the
  moment a key is available.

## Known limitations to fix before this is real

1. **Project path decoding is lossy.** Claude Code encodes the absolute
   project path into the folder name by turning every `/` into `-`. If
   your real project path already contains a dash (e.g.
   `variant-caller`), there's no way to tell that dash apart from a
   path separator just from the folder name. Fix: cross-reference
   against `git remote -v` / actual folders on disk, or read the
   `cwd` field Claude Code also logs per session (worth checking your
   real files for this — I didn't fabricate it in the synthetic data).
2. **Cursor is not next — it's last.** Its chat history lives in an
   undocumented SQLite blob (`state.vscdb`) that several independent
   teams had to reverse-engineer, and the schema has already changed
   across Cursor versions. Doable, but higher-maintenance than Claude
   Code or Codex.
3. **Codex CLI logs a similar JSONL format** under `~/.codex/sessions/`
   — phase 2, should reuse ~80% of this parser's logic once we confirm
   the exact field names against a real Codex log from your machine.
4. **No publish step yet.** Right now it just writes a local `.md` file.
   Next decision: does the post go to your own site, a Gamma deck, X,
   or somewhere else first?

## Next step

Run this against your real `~/.claude/projects/` today and send me the
output (or the raw file structure if something doesn't parse) — that's
the actual test of whether this holds up outside synthetic data.
