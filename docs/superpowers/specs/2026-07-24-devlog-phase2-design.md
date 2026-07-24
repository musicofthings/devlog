# Daily Dev Log — Phase 2 Design (Codex + Cursor Sources)

**Date:** 2026-07-24  
**Status:** Approved (architecture + approach 2 confirmed in planning)  
**Scope:** Real Codex and Cursor agent-transcript parsers; multi-root CLI; default merge of all three sources into one daily post.

---

## 1. Problem & goal

Phase 1 ships Claude Code only. The user runs Codex and Cursor on the same days; those sessions are invisible to the daily post.

**In scope**
- Real `codex` and `cursor` `SourceParser` implementations (replace stubs)
- Per-source data roots (`--claude-root`, `--codex-root`, `--cursor-root`)
- Default `--sources claude_code,codex,cursor` → one merged daily post
- Synthetic fixtures + unit tests + offline eval cases (including multi-source day)
- Compact digests include a short `src=` tag for attribution
- Manual dry-run against real `~/.codex` and `~/.cursor`

**Out of scope**
- Cursor SQLite / Composer `state.vscdb`
- Cross-tool session dedup/merge
- Publishing integrations, public feed, multi-user

---

## 2. Architecture

Unchanged pipeline; only plugins and CLI root routing change:

```
CLI (per-source roots)
  → SourceParser.iter_sessions(root) → RawSession[]
  → digest.slice_for_date(...) → SessionDigest[]
  → summarize.generate_post(...) → one markdown post
```

Day slicing remains only in `digest.slice_for_date`. Plugins emit unsliced `RawSession` with `source` set.

| Source | Default root | On-disk layout |
|--------|--------------|----------------|
| `claude_code` | `~/.claude` | `projects/*/*.jsonl` (unchanged) |
| `codex` | `~/.codex` | `sessions/YYYY/MM/DD/rollout-*.jsonl` |
| `cursor` | `~/.cursor` | `projects/*/agent-transcripts/**/*.jsonl` |

---

## 3. Codex parser

**Authoritative project path:** `session_meta.payload.cwd`.

**Events to capture**
- User text: prefer `event_msg` with `payload.type == "user_message"`; also `response_item` messages with `role == "user"` when content is not system/env chrome (`<environment_context>`, `<recommended_plugins>`, etc.)
- Tools: `response_item` with `payload.type` in `custom_tool_call`, `function_call` — record `name`; best-effort extract file path / shell command from `input` when present
- Tokens: `event_msg` `token_count` → `input_tokens` / `output_tokens` / `cached_input_tokens` when available
- Timestamps: top-level ISO `timestamp` on each JSONL line; session bounds = min/max

**Resilience:** skip malformed lines; omit sessions with no usable timestamps/events.

---

## 4. Cursor parser (agent transcripts only)

**Layout:** `~/.cursor/projects/<encoded-workspace>/agent-transcripts/<uuid>/<uuid>.jsonl`

**Project path**
1. Decode folder name with Windows drive-letter handling  
   (`c-Users-shibi-Projects-devlog` → `C:/Users/shibi/Projects/devlog`)
2. Prefer an explicit workspace path if found in early user content; else folder decode

**Events**
- `role == "user"`: extract text; strip/ignore chrome; prefer text inside `<user_query>...</user_query>` when present
- Timestamps: parse `<timestamp>...</timestamp>` when present; else fall back to file mtime for bounds only
- `role == "assistant"`: `tool_use` blocks → tool name + `path` / `command` / `file_path` / `target_directory` from input when present
- Session id = transcript file stem (uuid)

**Non-goal:** VS Code SQLite chat DBs.

---

## 5. CLI

| Flag | Default | Used by |
|------|---------|---------|
| `--sources` | `claude_code,codex,cursor` | all |
| `--claude-root` | `~/.claude` | `claude_code` |
| `--codex-root` | `~/.codex` | `codex` |
| `--cursor-root` | `~/.cursor` | `cursor` |

Missing root for one source: skip that source (verbose note); continue with others. Do not fail the whole run.

---

## 6. Digests

Compact `build_raw_digest` session headers include `src=<source>` so multi-source Claude narration can attribute tools without inventing detail. Full mode already includes `source=`.

No cross-source dedup: same project in Claude + Cursor the same day → two digests, one post.

---

## 7. Testing & acceptance

**pytest:** Codex/Cursor path resolution, malformed lines, tool extraction, CLI defaults, missing-root continuity.

**evals:** Codex day, Cursor day, multi-source same-day merged template post (plus existing Claude cases).

**Acceptance**
1. Default dry-run finds sessions from all three tools when present
2. Codex resolves real `cwd`
3. Cursor resolves decoded project paths from agent-transcript folders
4. Multi-source day → single post; digests retain distinct `source` values
5. Offline evals + pytest green; ruff clean
6. Manual real-log dry-run validated by user

---

## 8. Deferred (Phase 3+)

- Cursor SQLite / Composer bubbles
- Cross-source dedup
- Publishing daily posts beyond local markdown
