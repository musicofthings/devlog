# Daily Dev Log — Phase 1 Design (Multi-Source Plugin Architecture)

**Date:** 2026-07-24  
**Status:** Approved for implementation planning  
**Scope:** Local CLI hardening with plugin architecture; Claude Code as first real source. GitHub + landing page deferred to a later stage.

---

## 1. Problem & goal

Solo developers using AI coding tools generate invisible daily activity. Phase 1 turns local session transcripts into one short, factual, first-person build-log post per calendar day.

**In scope now**
- Multi-source plugin architecture (Claude Code implemented; Codex/Cursor stubs)
- Correct project-path resolution, local-timezone days, midnight time-slicing
- Template + Claude summarization with resilient fallback
- `--dry-run`, pytest suite, synthetic fixtures
- Local validation against real `~/.claude` (manual acceptance)

**Out of scope (next stage)**
- GitHub publish, landing page, public feed, multi-user, Codex/Cursor full parsers

---

## 2. Architecture

Shared core is source-agnostic. Each AI tool is a parser plugin that emits the same `SessionDigest` shape.

```
devlog/
  models.py              # SessionDigest (shared)
  digest.py              # day aggregation, midnight time-slicing
  summarize.py           # template + Claude narration
  cli.py                 # argparse entrypoint
  sources/
    base.py              # SourceParser protocol
    claude_code.py       # Phase 1 — real implementation
    codex.py             # Phase 2 — stub (returns [])
    cursor.py            # Phase 3 — stub (returns [])
main.py                  # thin wrapper -> cli.main()
tests/
sample_data/claude_code/ # fixtures mirroring ~/.claude/projects layout
```

### SourceParser contract

- `name: str` → `"claude_code" | "codex" | "cursor"`
- `iter_sessions(root: Path) -> Iterable[RawSession]` — parse transcripts into full (unsliced) sessions
- CLI loads enabled sources, then **`digest.slice_for_date(...)`** applies day clipping once for all sources
- Day slicing is never reimplemented inside a plugin; plugins only understand their on-disk format
- CLI `--sources` accepts a comma-list; default `claude_code`
- Stubs register and return `[]` so the plugin surface is real without fake parsers

---

## 3. Data model & day slicing

### SessionDigest

| Field | Behavior |
|---|---|
| `session_id`, `project_path`, `source` | Identity; `source` = plugin name |
| `start_time` / `end_time` | Clipped to that day's window |
| `duration_minutes` | From clipped times only |
| `user_messages`, `tool_calls`, `files_touched`, `bash_commands` | Only events whose timestamps fall inside that day |
| `tokens_in` / `tokens_out` / `tokens_cache_read` | Summed only from assistant events inside that day |

### Day boundaries

- A “day” is defined in the **local system timezone** (not hardcoded UTC)
- Whole-session “start OR end on date” attribution is removed
- A session spanning midnight becomes **two day-slices** with non-overlapping duration, events, and tokens

### Project path resolution (priority order)

1. `cwd` field from transcript events (authoritative when present)
2. Best-effort common root from tool file paths
3. Lossy folder-name decode (`-` → `/`) as last resort

---

## 4. Summarization & output

1. Aggregate the day’s `SessionDigest` list into a factual raw digest (no narrative)
2. If `ANTHROPIC_API_KEY` is set, call Claude (`claude-sonnet-4-6`) with a constrained system prompt (3–5 sentences, no invented detail)
3. On missing key or any API failure → deterministic template summary
4. Always print the post
5. Write `devlog-YYYY-MM-DD.md` unless `--dry-run`

---

## 5. CLI

| Flag | Behavior |
|---|---|
| `--date today\|YYYY-MM-DD` | Target day in local system timezone |
| `--sources` | Comma-list; default `claude_code` |
| `--claude-root` | Claude Code data root (default `~/.claude`) |
| `--dry-run` | Print only; do not write the markdown file |
| `--verbose` | Warn on skipped/unreadable sessions |
| `--sample-mode` | Use fixture layout under `sample_data/claude_code/` |

Write behavior: print always, write file immediately (no interactive prompt). Review safety is `--dry-run`.

---

## 6. Error handling

| Condition | Behavior |
|---|---|
| Malformed JSONL line | Skip line, continue |
| Empty / unreadable session file | Skip; optional `--verbose` warning |
| Missing source root | Clear message; exit 0 with “no activity” |
| Summarization unavailable/fails | Template fallback; pipeline continues |
| Unknown `--sources` name | Exit 2; list registered plugins |

---

## 7. Testing

**pytest (Phase 1)**
- Parser: malformed lines, empty sessions, `cwd` preferred over folder decode, dash-ambiguous names
- Day slicing: midnight-spanning session → two slices, no double-counted duration/tokens
- Local TZ: date filters use system timezone
- Digest → template: stable factual snapshot
- Claude path: mocked API — doesn’t crash; output is 3–5 sentences (≤120 words)
- Plugin registry: `claude_code` works; stubs return `[]`
- CLI: `--dry-run` prints but does not write

Fixtures under `sample_data/claude_code/`, including a multi-day spanning session. Real `~/.claude` validation is manual acceptance after the suite passes.

---

## 8. Migration from prototype

Existing flat modules (`claude_code_parser.py`, `summarize.py`, `main.py`) move into the `devlog/` package layout above. Behavior is preserved and extended (path/`cwd`, TZ, slicing, plugins, `--dry-run`, tests). Sample data relocates under `sample_data/claude_code/`.

---

## 9. Acceptance criteria (Phase 1 done)

- [ ] Plugin architecture in place; Claude Code is the only live source
- [ ] Project path correct via `cwd` (or documented fallback) for real project folder names
- [ ] Midnight sessions sliced without double-counting across two days
- [ ] Day boundaries use local system timezone
- [ ] Template and Claude-summarized paths both produce 3–5 sentence posts with no fabricated detail
- [ ] `--dry-run` works; pytest suite covers the cases above
- [ ] Manual run against real `~/.claude/projects/` for ≥3 calendar days across ≥2 projects (user-validated)

---

## 10. Deferred stages

1. **Local real-log validation** (manual, after this implementation)
2. **GitHub + landing page** (product surface; separate design)
3. **Codex parser** (`~/.codex/sessions/`, confirm schema against real logs)
4. **Cursor parser** (SQLite `state.vscdb`; version-gated, last)
