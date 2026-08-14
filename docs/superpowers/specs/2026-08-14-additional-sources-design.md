# Daily Dev Log — Additional coding-agent sources

**Date:** 2026-08-14  
**Status:** Implemented  
**Scope:** Extra `SourceParser` plugins so one daily post can include Grok, Copilot CLI, OpenCode, Warp, Vitreous, and a registered Antigravity stub — same Phase 2 pipeline.

---

## 1. Pipeline (unchanged)

```
SourceParser.iter_sessions(root) → RawSession[]
  → digest.slice_for_date(...)     # the only day-slicing step
  → one merged daily post
```

Plugins emit unsliced `RawSession` with `source` set. Missing roots are skipped; other sources still run. Malformed JSONL lines are skipped. Sessions with no usable timestamps/events are omitted.

---

## 2. What shipped

| Source | Kind | Default root | On-disk layout |
|--------|------|--------------|----------------|
| `grok` | Real parser | `~/.grok` | `sessions/<url-encoded-cwd>/<uuid>/chat_history.jsonl` + `summary.json` + `events.jsonl` |
| `copilot` | Real parser | `~/.copilot` | `session-state/<uuid>/events.jsonl` |
| `opencode` | Skip-empty | `%LOCALAPPDATA%/opencode` or `~/.local/share/opencode` | SQLite `opencode.db` (`session` / `message` / `part`); legacy JSON under `storage/session/` |
| `warp` | Skip-empty | `%LOCALAPPDATA%/warp/Warp` | `data/warp.sqlite` tables `agent_conversations`, `ai_queries`, `agent_tasks` |
| `vitreous` | Skip-empty | `~/.vitreous` | `sessions/*.jsonl` when the desktop persists transcripts |
| `antigravity` | Deferred stub | `~/.gemini` | Registered; returns `[]` unless plaintext `.jsonl` appears |

**Grok:** Prefer `summary.json` `cwd` / `git_root_dir`; else URL-decode the parent folder. User text from `type=user` with `<user_query>`; skip `system` and `synthetic_reason` chrome. Tools from assistant `tool_calls` (name + path/command); timestamps from `events.jsonl` `tool_completed.tool_call_id`.

**Copilot:** `session.start` supplies cwd/gitRoot. `user.message` with `source=user`; skip system/skill chrome. Tools from `tool.execution_start` (and `assistant.message.toolRequests` if not duplicated).

**OpenCode / Warp:** stdlib `sqlite3`, readonly URI. Zero rows → no sessions (not an error). Warp on this machine has empty agent tables because `cloud_conversation_storage` is on.

**Vitreous:** Do not parse `~/.vitreous/nvidia-skills` as sessions. Persistence is not shipped yet.

**Antigravity:** Conversations are protobuf/encrypted under `antigravity-cli` / `antigravity-ide`. No fake protobuf decoder. Needs local decryptable transcripts before a real parser.

---

## 3. Config / CLI

`DEFAULT_SOURCES` is:

`claude_code,codex,cursor,grok,copilot,opencode,warp,vitreous,antigravity`

New flags: `--grok-root`, `--copilot-root`, `--opencode-root`, `--warp-root`, `--vitreous-root`, `--antigravity-root`.

Old `config.toml` without the new keys still loads (defaults). `DevlogConfig.root_for()` maps each source. `devlog init` prompts for each root.

---

## 4. Deferred / not stubbed

- Antigravity protobuf/encrypted store (needs decryptable transcripts)
- Vitreous in-memory desktop transcripts (JSONL persistence not shipped)
- Cursor-style SQLite for Cursor (already out of scope)
- Aider, gemini-cli, Cline, Continue, Windsurf, Amazon Q, Amp, Crush, Goose — not present on disk; not stubbed
