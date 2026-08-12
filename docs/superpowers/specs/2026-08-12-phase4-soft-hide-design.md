# Daily Dev Log — Phase 4 Design (Soft-hide & admin confidence)

**Date:** 2026-08-12  
**Status:** Approved for implementation  
**Scope:** Make `publish_mode=auto` safer: reverse visibility without hard delete; diagnose admin/publish failures; optional lightweight review gate.

---

## 1. Problem & goal

Auto-publish puts posts on the public feed with no human gate. Hard `devlog delete` is irreversible from the live site forward. Operators need:

1. Soft-hide / unhide (exclude from public feed; keep `posts/*.md`)
2. Live admin progress after workflow dispatch (Actions run status)
3. Publish failure recovery (local commit reset / artifact rollback — Part A)
4. Optional review gate before auto-push (FR10-shaped)

**Out of scope:** multi-user auth, new transcript sources, recruiter redesign, cross-posting, full browser JS harness.

---

## 2. Soft-hide model

Sidecar file at repo root (same family as `.devlog-status.json`):

```json
{
  "dates": ["2026-07-20", "2026-08-01"]
}
```

| Behavior | Detail |
|---|---|
| Hide | Add date to `.devlog-hidden.json`; rebuild site; commit+push (same git tail as delete). Markdown stays in `posts/`. |
| Unhide | Remove date; rebuild; commit+push. |
| Public feed | Omits hidden dates; day HTML is not generated (stale day pages pruned). |
| Admin | Visible posts get Hide; admin panel lists currently hidden dates with Unhide. |

CLI:

```
devlog hide --date YYYY-MM-DD [--dry-run]
devlog unhide --date YYYY-MM-DD [--dry-run]
```

GitHub Actions: extend `delete-post.yml` with optional `action` input (`delete` \| `hide` \| `unhide`, default `delete`) so one workflow serves admin dispatch without renaming the workflow file (existing tokens/docs keep working).

---

## 3. Admin Actions progress

After a successful `workflow_dispatch` (HTTP 204), the admin panel:

1. Records `Date.now()` and polls `GET /repos/{repo}/actions/workflows/delete-post.yml/runs?event=workflow_dispatch&per_page=5`
2. Picks the newest run created at/after dispatch (small skew tolerance)
3. Polls that run until `status === "completed"`, then reports `conclusion` (success / failure / …)

Token already requires Actions read+write; read is enough for run status.

---

## 4. Publish recovery

Shipped in Part A: `publish.py` mirrors `delete_cmd` recovery via `GitPublishError` — reset unpushed publish commits; roll back artifacts on pre-commit failure so `_ensure_managed_paths_clean` does not wedge forever.

---

## 5. Review gate (`publish_mode=review`)

Adds `review` to `PUBLISH_MODES` (alongside `auto` / `pr` / `manual`):

- `review`: generate + write + rebuild like manual; do **not** push. Print confirm hint.
- `devlog publish --confirm --date YYYY-MM-DD`: push an already-written post (no regenerate) using the auto commit/push path with the same recovery.

This closes the FR10-shaped gap without inventing a separate queue service. Nightly schedule with `review` leaves files ready for human confirm.

---

## 6. Smoke / tests

- Unit tests: hide/unhide feed omission, CLI dry-run, publish recovery (Part A), API redact (Part A), review `--confirm`
- String/smoke tests: admin JS includes poll helpers + hide/unhide dispatch; workflow YAML validates `action` input
- Slash/skills parity for `devlog-hide` / `devlog-unhide` (or combined hide skill) across Claude/Cursor/Grok/`.agents`
