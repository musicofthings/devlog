# Phase 2 Codex + Cursor Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Codex/Cursor stubs with real parsers, multi-root CLI defaults merging all three sources into one daily post, with fixtures and evals.

**Architecture:** Keep Phase 1 pipeline (`iter_sessions` → `slice_for_date` → `generate_post`). Implement `devlog/sources/codex.py` and `cursor.py`; route roots in `cli.py`; add `sample_data/{codex,cursor}/` and eval cases.

**Tech Stack:** Python 3.11+, pytest, existing `devlog` package, ruff.

## Global Constraints

- Day slicing only in `digest.slice_for_date` — never inside plugins
- Cursor = agent-transcript JSONL only (no SQLite)
- No cross-tool session merge/dedup
- Default `--sources` = `claude_code,codex,cursor`
- Skip malformed JSONL lines; omit empty sessions
- Compact digests must include `src=<source>`

---

## File map

| File | Role |
|------|------|
| `devlog/sources/codex.py` | Codex rollout parser |
| `devlog/sources/cursor.py` | Cursor agent-transcript parser |
| `devlog/cli.py` | Per-source roots + new default sources |
| `devlog/digest.py` | Compact `src=` tag |
| `sample_data/codex/...` | Synthetic Codex fixtures |
| `sample_data/cursor/...` | Synthetic Cursor fixtures |
| `tests/test_codex_parser.py` | Codex unit tests |
| `tests/test_cursor_parser.py` | Cursor unit tests |
| `tests/test_cli.py` | CLI multi-root / defaults |
| `tests/test_digest.py` | Compact source tag |
| `evals/cases.py` | New offline cases |
| `README.md` | Docs + results |

---

### Task 1: Codex parser + fixtures + tests

- [ ] Create minimal `sample_data/codex/sessions/2026/07/20/rollout-*.jsonl` with `session_meta` (cwd), `user_message`, `custom_tool_call`/`function_call`, `token_count`, one malformed line
- [ ] Write failing tests in `tests/test_codex_parser.py` (cwd, tools, malformed skip, iter under root)
- [ ] Implement `devlog/sources/codex.py`
- [ ] Run `pytest tests/test_codex_parser.py` — pass

### Task 2: Cursor parser + fixtures + tests

- [ ] Create `sample_data/cursor/projects/c-Users-dev-code-devlog/agent-transcripts/<uuid>/<uuid>.jsonl`
- [ ] Write failing tests (path decode, user_query extract, tool_use, malformed)
- [ ] Implement `devlog/sources/cursor.py`
- [ ] Run `pytest tests/test_cursor_parser.py` — pass

### Task 3: Multi-root CLI + compact digest source tag

- [ ] Update `cli.py`: `--codex-root`, `--cursor-root`; default sources all three; per-source root map; skip missing roots
- [ ] Update `digest.py` compact headers to include `src=`
- [ ] Update `tests/test_cli.py`, `tests/test_digest.py`, `tests/test_sources_registry.py` as needed
- [ ] Run related pytest — pass

### Task 4: Evals + README + real-log smoke

- [ ] Add eval cases: Codex day, Cursor day, multi-source merge
- [ ] Update README defaults/flags/results
- [ ] Run full pytest, `python -m evals.run`, ruff; dry-run against real roots for one dated day

---

## Verification

```bash
pytest -q
python -m evals.run
python -m ruff check .
python main.py --date 2026-07-20 --dry-run --verbose
```
