# Daily Dev Log — PRD / TRD

Status: Phase 1 prototype validated against synthetic data. Not yet run
against real logs. This doc is meant to be handed to Claude Code as the
spec to continue building from.

---

## 1. Problem / Product Summary

Solo developers who use AI coding tools (Claude Code, Codex, Cursor)
generate a huge amount of daily activity that's invisible to anyone
outside their terminal. There's no lightweight, automatic way to turn
"what I actually did today" into something shareable — existing tools
either (a) track usage stats privately for cost/governance (Torii,
Worklytics), or (b) show raw stats publicly without narrative (WakaTime).

**Goal:** one short, factual, auto-generated first-person post per day,
summarizing a developer's actual AI-assisted coding activity, suitable
for a public "build log" feed that employers/recruiters could browse.

**Non-goals for this phase:** publishing/hosting, multi-user accounts,
recruiter-facing UI, monetization. This phase is single-user, local,
CLI-only — prove the pipeline before building a product around it.

## 2. User

Primary user (v1): the developer themself, running this against their
own machine, for their own review. No multi-tenant concerns yet.

## 3. Functional Requirements

### 3.1 Data ingestion (Phase 1 = Claude Code only)
- FR1: Read all Claude Code session transcripts under `~/.claude/projects/**/*.jsonl`.
- FR2: For a given calendar date (UTC), identify every session with any
  activity that day (session start OR end falls on that date — a
  session spanning midnight should count for both days it touches).
- FR3: Per session, extract:
  - project path (best-effort decoded from folder name)
  - session start time, end time, duration
  - every user-authored task message (the actual instructions given)
  - every tool call: tool name, file path (if any), bash command (if any)
  - token usage: input, output, cache_read, cache_creation
- FR4: Tolerate malformed/partial lines (e.g. a session file still being
  written) without crashing — skip the bad line, keep parsing.

### 3.2 Digest construction
- FR5: Aggregate all of a day's sessions into one digest: total active
  minutes, list of projects touched, per-session task list, per-session
  tool-call counts, all files touched, all bash commands run.
- FR6: The digest must be purely factual/structured — no narrative
  generation happens at this stage. This keeps the LLM step auditable:
  anything in the final post should be traceable back to a specific
  digest line.

### 3.3 Summarization
- FR7: If `ANTHROPIC_API_KEY` is set, send the digest to Claude
  (model: claude-sonnet-4-6) with a system prompt constraining it to:
  plain language, 3-5 sentences, no invented details, specific project/
  file references over generic praise.
- FR8: If no API key is available, or the API call fails for any reason
  (auth, network, rate limit), fall back to a deterministic template
  summary — the pipeline must never hard-fail just because summarization
  is unavailable.

### 3.4 Output
- FR9: Write the resulting post to `devlog-YYYY-MM-DD.md` in the working
  directory.
- FR10 (not yet built): a way to review/edit the post before it's
  considered "final" — v1 users should be able to see and tweak the
  auto-generated post, not have it silently published.

## 4. Technical Requirements / Architecture

```
claude_code_parser.py   -- FR1-FR4: JSONL -> SessionDigest objects
summarize.py            -- FR5-FR8: SessionDigest list -> raw digest -> post
main.py                 -- CLI glue + FR9
sample_data/            -- synthetic fixtures for testing without real logs
```

### 4.1 Data model
```python
SessionDigest:
    session_id: str
    project_path: str
    start_time: datetime
    end_time: datetime
    user_messages: list[str]
    tool_calls: dict[str, int]      # tool name -> count
    files_touched: set[str]
    bash_commands: list[str]
    tokens_in / tokens_out / tokens_cache_read: int
```

### 4.2 Known technical debt / open problems for Claude Code to solve next

1. **Lossy project-path decoding (highest priority fix).** Claude Code
   folder-encodes the absolute project path by replacing `/` with `-`.
   A real project name containing a dash is ambiguous to decode.
   Action: inspect a real `~/.claude/projects/<folder>/*.jsonl` file for
   a `cwd` field logged per session/event — if present, use that as the
   authoritative project path instead of decoding the folder name.

2. **Multi-day session boundaries.** A session that runs past midnight
   UTC currently gets attributed to both days via a simple
   "start OR end matches target date" check — this will double-count
   duration/tokens if a post is generated for both days. Needs a proper
   per-day time-slicing of session activity, not whole-session
   attribution.

3. **Timezone handling.** Everything is currently UTC. Confirm what
   timezone the user actually wants "today" measured in (likely IST,
   Hyderabad) and make this configurable rather than hardcoded.

4. **Phase 2: Codex CLI support.** Codex writes similar JSONL rollouts
   under `~/.codex/sessions/`. Needs its own parser module mirroring
   `claude_code_parser.py`, with field names confirmed against a real
   Codex log (don't assume identical schema to Claude Code).

5. **Phase 3: Cursor support.** Chat history lives in an undocumented
   SQLite `state.vscdb` (table `cursorDiskKV` or `ItemTable` depending on
   version, keys `composerData:<id>` / `bubbleId:<composerId>:<bubbleId>`).
   Schema has changed across Cursor versions and there's no official API
   — treat this as the highest-maintenance-cost source and build it last,
   behind a clear version check.

6. **No test suite yet.** The only validation so far is the manual run
   against `sample_data/`. Needs actual `pytest` unit tests: parser
   correctness on malformed lines, midnight-boundary sessions, empty
   sessions, and a snapshot test on the digest→template path (the
   Claude-summarization path is inherently non-deterministic and should
   be tested for "doesn't crash" + "stays under N words", not exact
   output).

7. **Review/edit step (FR10) is unbuilt.** Right now the script writes
   the file and exits. Needs a minimal interactive step (or at least a
   `--dry-run` flag) before this is safe to wire to any actual
   publishing target.

## 5. Acceptance Criteria for "Phase 1 done"

- [ ] Run against the user's real `~/.claude/projects/` for at least 3
      different real calendar days, across at least 2 different real
      projects, and produce a post that accurately reflects what
      happened (validated by the user, not just "it didn't crash").
- [ ] Project path is correctly identified (not garbled by dash-decoding)
      for all of the user's actual project folder names.
- [ ] Midnight-boundary sessions are attributed correctly (no double
      counting across two days).
- [ ] Fallback template path and Claude-summarized path both produce a
      post that fits in 3-5 sentences and contains no fabricated detail.

## 6. Explicitly out of scope for now

- Any public-facing profile, feed, or recruiter-facing UI
- Multi-user support / auth
- Publishing integrations (own site, X, Gamma, etc.)
- Codex and Cursor ingestion (tracked above as future phases, not blocking)
