# Daily Dev Log — Calendar ICS feed (v1)

**Date:** 2026-08-14
**Status:** Draft for review (not implemented)
**Scope:** Publish a subscribe-able iCalendar feed of visible daily posts so Google Calendar, Apple Calendar, Outlook.com, and Microsoft 365 can overlay the build-log. Same per-person Pages site as today — not a shared multi-tenant calendar.

---

## 1. Problem & goal

The public log is a GitHub Pages HTML feed (`docs/log/`). Friends and a manager do not live in that tab. They *do* look at calendars. The operator wants a daily “I shipped a log” mark on the calendars people already open, without sending transcripts (or OAuth tokens) to Google/Apple/Microsoft.

**In scope**
- Local generation of one `.ics` file during `rebuild_site()`, hosted on GitHub Pages
- One all-day transparent event per **visible** published day (same set as the HTML feed)
- Subscribe UX for Google, Apple/iOS, Outlook.com personal, Outlook / M365 work
- Privacy-safe event text (link-only description by default)
- Hide / delete / unhide stay in lockstep because they already call `rebuild_site()`
- Fork-friendly: derive the Pages URL from `origin`, like the admin delete panel

**Out of scope (do not build yet)**
- OAuth push into a primary calendar (Google Calendar API, Apple EventKit/CalDAV, Microsoft Graph)
- Reminders / `VALARM`, lock-screen alerts, mobile widgets
- Timed events from `active_minutes` / session intervals
- Per-session events, meeting invites (`METHOD:REQUEST`)
- Tokenized / unlisted ICS on a public repo (secret would live in git)
- RSS/Atom (sibling feed; not required for calendars)
- Changing transcript parsers or the in-flight additional-sources work

---

## 2. Recommended v1 vs alternatives

### A. Public ICS subscription feed (recommended)

`devlog publish` / hide / delete / unhide → `rebuild_site()` writes `docs/log/calendar.ics` → Pages serves `https://<user>.github.io/<repo>/log/calendar.ics` → each subscriber adds that URL as a **secondary** calendar overlay.

| Why this wins for v1 | |
|---|---|
| Same trust model as the site | Only the short post (plus a URL) is public. Transcripts never leave the laptop. CI still only deploys committed `docs/`. |
| Zero vendor apps | No Google Cloud project, no Entra app registration, no Apple developer account, no refresh-token store in `~/.config/devlog`. |
| Works for other people | Each operator has their own GitHub + Pages + ICS. A friend or manager subscribes to *that* URL. No multi-tenant calendar service. |
| Hide/delete already rebuild | No new git tail. `docs/log/` is already in `MANAGED_PATHS`. |
| Outlook work reality | “Subscribe from web” is the path that does **not** need IT to approve an app. Graph write *does*. |
| Extra clients for free | Fastmail, Proton Calendar, Thunderbird, Nextcloud all consume ICS URLs. |

**Costs:** clients poll (Google ~daily, Apple as rarely as weekly unless the user sets a shorter interval). Stale events can linger until the next fetch. Events are read-only overlays, not native meetings — that is the desired “show off a log,” not “block my afternoon.”

### B. Alternative: per-provider OAuth push (Google + Graph + CalDAV)

CLI obtains tokens and inserts/updates a VEVENT (or Graph event) in the user’s primary calendar.

Reject for v1: four auth stacks, token files next to config, tenant admin consent for M365, EventKit is not a Windows CLI, primary-calendar clutter looks like meetings, hide/delete must call each API or leave orphans, and every new operator must register apps. Revisit only if subscribe-from-URL is blocked by a specific tenant *and* native events are still required.

### C. Alternative: one-shot `.ics` download (import, not subscribe)

Generate a file the user imports once. Reject: hide/delete/`--force` never update subscribers. Fine as a manual “File → Import” escape hatch (the same `calendar.ics` URL already downloads), not as the product.

**v1 default is A.** Do not implement B or C as separate features.

---

## 3. Architecture (fits today’s pipeline)

```
posts/*.md  +  .devlog-hidden.json
        ↓
rebuild_site()          # already used by publish, hide, unhide, delete
  docs/log/index.html
  docs/log/YYYY-MM-DD.html   (visible days only; stale HTML pruned)
  docs/log/calendar.ics      # NEW: same `visible` list
        ↓
publish_mode auto/pr/manual/review  (unchanged git tail)
        ↓
pages.yml deploys docs/ → https://<user>.github.io/<repo>/log/calendar.ics
```

Obsidian stays private and is **not** a calendar source. The ICS is a public projection of the Pages feed, one feed per person’s site.

New module: `devlog/calendar.py` (keep ICS escaping / folding out of the already-large `site.py`). `rebuild_site()` calls it after HTML, appends the path to the returned `written` list.

No new CLI command. Optional: after a successful publish, print the subscribe URL when origin is GitHub.

---

## 4. Event shape

One `VEVENT` per visible day. Not a timed block.

| Field | v1 value | Why |
|---|---|---|
| `SUMMARY` | `Dev log · YYYY-MM-DD` | Short enough for lock screen / month view. No project name (posts often contain paths and prompt text). |
| `DTSTART` | `VALUE=DATE:YYYYMMDD` | All-day. |
| `DTEND` | next calendar day (`VALUE=DATE`, exclusive) | RFC 5545 all-day convention. |
| `DTSTAMP` | rebuild time, UTC | |
| `UID` | `devlog-YYYY-MM-DD@<owner>.github.io.<repo>` | Stable across rebuilds so clients replace, not duplicate. Repo in the UID avoids collisions if one GitHub user has two logs. |
| `SEQUENCE` | `0` | No sidecar sequence store in v1; UID + DTSTAMP is enough for subscribe feeds. |
| `URL` | absolute day page, e.g. `https://musicofthings.github.io/devlog/log/2026-08-13.html` | Calendar “open event” target. |
| `DESCRIPTION` | **default: that same URL only** | Calendar surfaces are more visible than a website (notifications, meeting view, screenshare). Do not paste the post body. |
| `TRANSP` | `TRANSPARENT` | Must not mark the subscriber busy. Critical for a work Outlook overlay. |
| `STATUS` | `CONFIRMED` | |
| `CLASS` | `PUBLIC` | Matches a public Pages site. |
| Alarms | none | A daily log must not ping. |

Do **not** use 09:00 local: it looks like a meeting, fights time zones, and is worse on a manager’s overlay. Do **not** use `active_minutes` / `active_intervals`: those are fragmented private session stats, not a single public appointment, and they leak when the operator actually coded.

v1 description mode is **link only** (no config key). `excerpt` / `body` are a later opt-in, not part of this spec.

### Calendar wrapper

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//devlog//Daily Dev Log//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
X-WR-CALNAME:Daily Dev Log
X-WR-CALDESC:Published daily build logs
X-PUBLISHED-TTL:PT12H
REFRESH-INTERVAL;VALUE=DURATION:PT12H
…VEVENTs…
END:VCALENDAR
```

CRLF line endings. Escape `\`, `;`, `,`, and newlines in text fields. Fold lines at 75 octets if a future `body` mode needs it; link-only descriptions will not.

### Pages URL

Same pattern as `detect_github_repo()`:

- `github.com/owner/repo` → `https://owner.github.io/repo` (no trailing slash), except when `repo == owner.github.io` → `https://owner.github.io`
- Day URL: `{site_url}/log/{date}.html`
- Feed URL: `{site_url}/log/calendar.ics`

If origin is missing or not GitHub, do not write `calendar.ics`; if a stale file exists from a previous origin, unlink it (same prune idea as leftover day HTML). Optional later: `site_url` in `config.toml` for a custom domain; not required for v1.

---

## 5. Hide / delete / unhide

Use the **same visibility set as the HTML feed**. No extra sidecar.

| Action | ICS |
|---|---|
| Publish / `--force` | Upsert VEVENT (same UID). |
| Hide | Date omitted from ICS (day HTML already omitted). |
| Unhide | VEVENT returns with the same UID. |
| Delete | Date omitted (`posts/*.md` gone; HTML pruned). |

Do **not** emit `STATUS:CANCELLED` tombstones in v1. They keep “cancelled” grey events on some clients, which fights hide’s intent (“this day is not public”). Document that subscribers may see a removed day until the client’s next poll (Google often ~24h; Apple/iOS sometimes weekly). If Apple orphans become a real complaint, v1.1 can add short-lived `CANCELLED` rows.

Empty visible set: still write a valid calendar with zero `VEVENT`s (so a hide-everything operator does not leave a stale full feed).

---

## 6. Privacy

Published HTML already can contain project paths and prompt text (`init` already warns). Calendar events are **more** exposed: lock screen, notification shade, Outlook meeting overlay, screenshare during a 1:1.

**Defaults**
- Description = Pages URL only (not post body, not excerpt, not tool counts).
- Title = `Dev log · date` only.
- `TRANSP:TRANSPARENT`.
- No alarms.
- Generating the file does not subscribe anyone; the operator (or a friend/manager) must paste the URL.

**Tokenized / unlisted feed:** skip on a public GitHub repo. A `feed-<secret>.ics` path is still in git history and on a public Pages site. Revisit only with a private host (private Pages + auth, or a worker in front of the file). Sharing “just with friends” in v1 = send them the public ICS URL; it is no more secret than `docs/log/index.html`.

**Obsidian:** vault notes stay local. Do not generate a second calendar from the vault.

---

## 7. Subscribe UX (operator docs + a link on the log page)

Public URL (this repo): `https://musicofthings.github.io/devlog/log/calendar.ics`

`webcal://musicofthings.github.io/devlog/log/calendar.ics` for Apple/iOS.

Add on `docs/log/index.html` (generated): “Subscribe in a calendar” with https + webcal hrefs, plus `<link rel="alternate" type="text/calendar" href="calendar.ics">`. Extend `_ensure_landing_nav` or the generated feed only — do not redesign the landing.

### Google Calendar

1. calendar.google.com → other calendars (left sidebar) **+** → **From URL**
2. Paste the `https://…/log/calendar.ics` URL → Add calendar
3. Rename the overlay if desired. It is a **separate** calendar, not the primary.

Notes: Google requires a publicly fetchable HTTP(S) URL (no auth). Refresh is typically about once per day; there is no reliable “force refresh” for URL calendars.

### Apple Calendar / iOS

- macOS Calendar: **File → New Calendar Subscription…** → paste https or `webcal://` URL. Set auto-refresh to hourly/daily (default can be weekly).
- iOS: Settings → Calendar → Accounts → **Add Account** → **Other** → **Add Subscribed Calendar**, or tap a `webcal://` link from Safari.
- iCloud will sync a subscribed calendar to other Apple devices if “Enable this calendar on my iPhone/Mac” stays on.

### Outlook.com (personal)

1. outlook.live.com → calendar → **Add calendar** → **Subscribe from web**
2. Paste the https ICS URL. Import as a new calendar (do not merge into the primary).

### Outlook / Microsoft 365 work — subscribe vs Graph, and “show the manager”

**v1 path: Subscribe from web** (Outlook on the web: Add calendar → Subscribe from web; Outlook desktop: add an Internet Calendar). Same public ICS. No Entra app, no admin-consented Graph permission.

**Do not use Microsoft Graph in v1.** Writing events into the work mailbox looks like native meetings, needs `Calendars.ReadWrite` (often admin consent for an org-owned app), and hide/delete must then delete remote events or they linger as real calendar items.

**IT / tenant limits.** Some organizations disable internet calendar subscriptions (`InternetWebCalendarSubscription` / sharing policy). If Subscribe from web is missing or fails, that is a tenant policy, not something the CLI can fix. Fallback: share the Pages log URL, or export/download `calendar.ics` once (stale). Do not silently fall back to Graph.

**“Show my manager” reality**
- Best: send the manager the ICS URL (or the log URL). They subscribe on **their** calendar as an overlay named “Shibi’s dev log.” They will see all-day marks without the operator’s primary calendar or mailbox.
- Weaker: overlay the ICS on **your** work calendar and screenshare in a 1:1. Subscribed internet calendars are often **not** included when you share your primary calendar with a colleague, so “I subscribed, so my manager will see it on my shared calendar” is frequently false.
- Overlay events must stay `TRANSPARENT` so Scheduling Assistant does not treat log days as busy.

---

## 8. Extra calendars (no extra work)

ICS subscribe also covers Fastmail (Settings → Calendars → Subscribe), Proton Calendar (subscribe to URL), Thunderbird (New → Calendar → On the Network → ICS), and Nextcloud. No provider-specific code.

---

## 9. Config / init / docs

v1 adds **zero new config keys**. Pages URL comes from `origin`; description is always the day URL. Do not add `calendar_enabled`, `site_url`, or `calendar_description` in this spec. Custom domain and excerpt/body description are a later config PR.

Update: `pages_checklist()` (subscribe URL + privacy line), README public URLs, maybe print the ICS URL at the end of `devlog publish`.

---

## 10. Tests

New `tests/test_calendar.py` (fixture posts, no network):

1. Visible days → one VEVENT each; `SUMMARY`, all-day `DATE`, exclusive `DTEND`, `TRANSP:TRANSPARENT`, `UID` stable across two builds
2. `DESCRIPTION` / `URL` are the absolute day page (link mode); post body must not appear
3. Hidden date omitted; unhide restores the same UID
4. Deleted date omitted; empty feed is a valid `VCALENDAR` with no VEVENTs
5. `rebuild_site` writes `docs/log/calendar.ics` and returns that path
6. ICS text uses CRLF; commas in a URL do not break unfolding (escape helper unit test)

Do not add browser tests. Do not call Google/Graph.

---

## 11. Implementation sketch (when approved)

1. `devlog/calendar.py`: `detect_pages_url(repo, git_run)`, `build_ics(visible_posts, site_url) -> str`, ICS escape
2. `rebuild_site`: after HTML, if `site_url` resolved, write `docs/log/calendar.ics`; include in `written`
3. Generated feed: subscribe link + `rel=alternate`
4. README + `pages_checklist()` subscribe steps (include the GitHub Pages poll lag and the “manager subscribes to the URL” note)
5. Tests above

Do not touch source parsers, Obsidian, or additional-sources work.

---

## 12. Acceptance

1. After publish, `docs/log/calendar.ics` is committed with the HTML and served on Pages.
2. Google / Apple / Outlook.com can subscribe by URL (manual check once).
3. Hide removes the day from both HTML and ICS on the next Pages deploy; unhide restores the same UID.
4. Delete removes the day from ICS.
5. Event title has no project path; description is the Pages URL.
6. Events are all-day and transparent (not busy).
7. A second GitHub user with their own `origin` gets their own feed URL with no hardcoded `musicofthings`.
