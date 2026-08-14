# Daily Dev Log — Phase 5 Design (Obsidian offline vault mirror)

**Date:** 2026-08-14  
**Status:** Approved for implementation  
**Scope:** Mirror each published post into a local Obsidian vault as an archive note plus a bounded Daily Note embed. GitHub Pages remains the public source of truth.

---

## 1. Problem & goal

Phase 3–4 publish a public GitHub Pages feed. Operators also keep a private Obsidian vault and want the same daily posts locally, without making the vault canonical or risking accidental note deletion.

**In scope**
- Optional `obsidian_vault` config (empty = current cloud-only behavior)
- `{vault}/{obsidian_folder}/YYYY-MM-DD.md` archive notes
- Bounded wikilink embed in that day's Daily Note
- `devlog publish` writes the mirror after a successful local `posts/` write
- `devlog obsidian --backfill` for existing `posts/*.md`
- Preserve-by-default on hard delete; opt-in vault removal

**Out of scope**
- Obsidian plugin, Dataview dashboards
- Bidirectional vault → GitHub sync
- Editing notes in Obsidian and pushing those edits to Pages
- Transcript parsers, public site redesign, hide/unhide touching the vault

---

## 2. Architecture

```
devlog publish
     ↓
posts/YYYY-MM-DD.md + rebuild docs/log/
     ↓
obsidian_vault set?  --no--> skip
     ↓ yes
DevLog/YYYY-MM-DD.md  +  Daily/YYYY-MM-DD.md (%%devlog region)
     ↓
publish_mode git (auto/pr) or stop (manual/review)
```

Vault files are never git-managed. A missing or unreadable vault must not fail GitHub publish.

---

## 3. Config

```toml
obsidian_vault = ""                 # empty = disabled
obsidian_folder = "DevLog"
obsidian_daily_folder = "Daily"     # "" = vault root
obsidian_on_delete = "preserve"     # preserve | remove
```

Missing keys in an existing `config.toml` use the defaults above. `devlog init` (including `--defaults`) auto-detects the currently open Obsidian vault from the app registry, or creates `~/Documents/DevLog` if none is found. Interactive init pre-fills that path (blank still skips).

---

## 4. Note format

**Archive** `{vault}/DevLog/2026-08-13.md` — overwrite on republish/`--force`:

```markdown
---
date: 2026-08-13
tags:
  - devlog
---

# 2026-08-13

<body from posts/YYYY-MM-DD.md after the title>
```

The markdown body matches `write_post_markdown` (title + post text). YAML frontmatter is vault-only.

**Daily Note** `{vault}/Daily/YYYY-MM-DD.md` — never replace the whole file. Upsert only:

```markdown
%%devlog
![[DevLog/2026-08-13]]
%%
```

- Missing daily note: create with `# YYYY-MM-DD` plus the region.
- Existing daily note: replace the region if present, else append.
- Opted-in delete: strip the region; leave the rest of the daily note.

---

## 5. CLI

| Command | Vault behavior |
|---|---|
| `devlog publish` | After a successful local post write, write/overwrite archive + upsert daily embed. `--dry-run` prints intended vault paths when enabled. |
| `devlog hide` / `unhide` | No vault changes. |
| `devlog delete` | Default: leave vault alone. Remove archive + daily region only if `obsidian_on_delete = remove` **or** `--also-obsidian`. Removal happens only after a successful git delete. |
| `devlog obsidian --backfill` | Mirror every `posts/*.md` (idempotent). `--date` for one day. `--dry-run` supported. |

`devlog run` does not write the vault.

---

## 6. Safety

- Empty `obsidian_vault`: skip silently.
- Vault path set but directory missing: warn; GitHub publish/delete still succeeds.
- Mirror write errors: warn; GitHub publish still succeeds.
- Soft-hide never touches Obsidian.
- Hard delete never removes vault notes unless the operator opts in.

---

## 7. Acceptance

1. Publish with vault unset writes `posts/` only.
2. Publish with vault set creates archive + daily embed; a second publish does not duplicate the region or clobber other daily-note text.
3. `--force` overwrites the archive body.
4. Hide does not touch vault files.
5. Delete with `preserve` keeps vault notes; `remove` / `--also-obsidian` deletes archive and strips the daily region.
6. Missing vault directory: publish still succeeds with a warning.
7. `devlog obsidian --backfill` copies existing posts into the vault.
