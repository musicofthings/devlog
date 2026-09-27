"""`devlog deck`: a slide-by-slide outline of a week, month, quarter, or project.

Output is Markdown with one `---` between slides, which Gamma ("Paste in
text", card-by-card), Marp, and most Markdown-to-slides tools split on. It is
built from the vault index, so it needs a vault that has been mirrored.

`--detail` works like `public_detail` (and defaults to it), because a deck
tends to travel further than the vault:

- summary: totals, kinds of work, streaks. No project names.
- projects: + time per project, commits and PRs counted, pipelines, and
  catalog topics (never your custom `[topics]`), open-thread counts.
- verbatim: + what you asked for, commit and PR titles, pipeline errors,
  open threads, and the papers you mentioned.

Everything passes through redaction (`redact_patterns`, `redact_presets`).
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from devlog.config import PUBLIC_DETAIL_LEVELS, DevlogConfig, default_config_path, load_config
from devlog.knowledge import headline_task, is_empty_day, project_slug
from devlog.literature import as_reference
from devlog.obsidian import _folder_root, vault_root
from devlog.pricing import estimate, merge_tokens, price_table
from devlog.privacy import configure_redaction, redact_sensitive_text
from devlog.topics import TopicDetector
from devlog.vault_graph import (
    _period_bounds,
    _project_names,
    all_references,
    all_runs,
    annotate,
    iso_week,
    load_index,
    load_state,
    month_of,
    quarter_of,
    safe_text,
    streaks,
    thread_key,
)
from devlog.worktypes import describe

SLIDE_BREAK = "\n\n---\n\n"
TOP_PROJECTS = 8
PER_PROJECT = 3
MAX_THREADS = 8
MAX_REFS = 8


def _hours(minutes: float) -> str:
    minutes = int(round(minutes))
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes} min"


def _clean(text: str) -> str:
    return redact_sensitive_text(" ".join(str(text).split()))


def resolve_scope(days: dict[str, dict], *, week=None, month=None, quarter=None,
                  since=None, until=None, today: date | None = None) -> tuple[str, date, date]:
    """(label, start, end). A period flag without a value means the latest logged one."""
    latest = date.fromisoformat(max(days)) if days else (today or date.today())
    for kind, value, labeler in (("week", week, iso_week), ("month", month, month_of),
                                 ("quarter", quarter, quarter_of)):
        if value is not None:
            label = labeler(latest.isoformat()) if value == "latest" else value
            start, end = _period_bounds(kind, label)
            return label, start, end
    if since or until:
        start = date.fromisoformat(since) if since else date.fromisoformat(min(days))
        end = date.fromisoformat(until) if until else latest
        return f"{start} – {end}", start, end
    label = iso_week(latest.isoformat())
    start, end = _period_bounds("week", label)
    return label, start, end


def _title(label: str, project: str | None) -> str:
    if "-W" in label:
        text = f"Week {label}"
    elif "-Q" in label:
        year, q = label.split("-Q")
        text = f"{year} Q{q}"
    elif len(label) == 7:
        text = date.fromisoformat(f"{label}-01").strftime("%B %Y")
    else:
        text = label
    return f"{project}: {text}" if project else f"{text} in review"


def build_deck(days: dict[str, dict], *, label: str, start: date, end: date,
               detail: str = "projects", project: str | None = None,
               detector: TopicDetector | None = None, done: set[str] | None = None,
               prices: dict | None = None) -> str:
    detector = detector or TopicDetector()
    done = done or set()
    days = annotate(days, detector)
    names = _project_names(days)
    display = {s: c.most_common(1)[0][0] for s, c in names.items()}
    in_range = {d: m for d, m in days.items() if start <= date.fromisoformat(d) <= end}

    slug = None
    if project is not None:
        want = project_slug(project)
        slug = want if want in display else next(
            (s for s, n in display.items() if want in s or project.lower() in n.lower()), None)
        if slug is None:
            raise ValueError(f"No project matching {project!r} in the vault")
        in_range = {d: {**m, "projects": [p for p in m.get("projects") or []
                                          if p["slug"] == slug],
                        "runs": [r for r in m.get("runs") or []
                                 if r.get("project_slug") == slug],
                        "threads": []}
                    for d, m in in_range.items()
                    if any(p["slug"] == slug for p in m.get("projects") or [])}

    active = {d: m for d, m in in_range.items() if not is_empty_day(m)}
    entries = [(d, p) for d in sorted(active) for p in active[d].get("projects") or []]
    named = detail != "summary"
    verbatim = detail == "verbatim"

    minutes_by: Counter = Counter()
    days_by: Counter = Counter()
    partial: set[str] = set()  # backfilled multi-project days have no per-project minutes
    for _d, p in entries:
        minutes_by[p["slug"]] += p.get("minutes") or 0
        days_by[p["slug"]] += 1
        if p.get("minutes") is None:
            partial.add(p["slug"])
    ranked = sorted(days_by, key=lambda s: (-days_by[s], -minutes_by[s], s))
    total_minutes = sum(int(m.get("active_minutes") or 0) for m in active.values())
    commits = [(d, p, c) for d, p in entries for c in p.get("commits") or []]
    prs: dict[tuple[str, int], tuple[str, dict, dict]] = {}
    for d, p in entries:
        for pr in p.get("pull_requests") or []:
            prs[(p["slug"], pr["number"])] = (d, p, pr)  # latest state wins
    runs = [(d, r) for d, r in all_runs(in_range)]
    work = Counter(w for m in active.values() for w in m.get("work_types") or [])
    _, longest = streaks(active) if active else (0, 0)
    project_name = display.get(slug, slug) if slug else None
    title = _title(label, _clean(project_name) if project_name and named else None)

    slides: list[list[str]] = []
    glance = [f"# {title}", "",
              f"{start:%d %b %Y} – {end:%d %b %Y} · from my build log", ""]
    if slug:
        total_minutes = minutes_by[slug]
    time_text = ("≥ " if slug in partial else "") + _hours(total_minutes) \
        if total_minutes else "unrecorded time"
    glance.append(f"- **{len(active)}** active day(s) of {(end - start).days + 1}, "
                  f"**{time_text}** of focused time")
    if not slug:
        glance.append(f"- **{len(minutes_by)}** project(s)")
    if commits:
        glance.append(f"- **{len(commits)}** commit(s) shipped")
    if prs:
        states = Counter(pr["state"] for _, _, pr in prs.values())
        glance.append(f"- **{len(prs)}** pull request(s): " + ", ".join(
            f"{n} {s}" for s, n in states.most_common()))
    if runs and named:
        ok = sum(r["status"] == "success" for _, r in runs)
        glance.append(f"- **{len(runs)}** pipeline run(s), {ok} succeeded")
    if longest > 1:
        glance.append(f"- Longest streak: **{longest}** days")
    if named and prices is not None:
        usd, _ = estimate(merge_tokens(*(p.get("tokens_by_model") for _, p in entries)), prices)
        if usd:
            glance.append(f"- API-equivalent model cost: **${usd:,.2f}**")
    slides.append(glance)

    if named and len(days_by) > 1:
        # Shares only when every project's time is known; otherwise "at least".
        exact = not partial
        spent = sum(minutes_by.values()) or 1
        rows = ["## Where the time went", "",
                "| Project | Days | Time |" + (" Share |" if exact else ""),
                "|---|---:|---:|" + ("---:|" if exact else "")]
        for s in ranked[:TOP_PROJECTS]:
            mins = minutes_by[s]
            shown = ("≥ " if s in partial else "") + _hours(mins) if mins else "—"
            share = f" {100 * mins / spent:.0f}% |" if exact else ""
            rows.append(f"| {_clean(display.get(s, s))} | {days_by[s]} | {shown} |{share}")
        if len(ranked) > TOP_PROJECTS:
            rows.append(f"\n…and {len(ranked) - TOP_PROJECTS} more")
        if partial:
            rows.append("\nTimes marked ≥ or — include days recorded before per-project "
                        "timing was kept.")
        slides.append(rows)

    if work:
        slides.append(["## Kinds of work", ""] + [
            f"- **{w}** ×{n} — {describe(w)}" for w, n in work.most_common(6)])

    if verbatim:
        highlights = ["## Highlights", ""]
        for s in ranked[:TOP_PROJECTS]:
            tasks: list[str] = []
            for _, p in reversed([e for e in entries if e[1]["slug"] == s]):
                task = headline_task(p.get("tasks") or [])
                if task and _clean(task) not in tasks:
                    tasks.append(_clean(task))
            if tasks:
                if not slug:
                    highlights.append(f"**{_clean(display.get(s, s))}**")
                highlights += [f"- {t}" for t in tasks[:PER_PROJECT]]
                highlights.append("")
        if len(highlights) > 2:
            slides.append(highlights)

    if named and (commits or prs):
        shipped = ["## Shipped", ""]
        for s in ranked[:TOP_PROJECTS]:
            mine = [c for _, p, c in commits if p["slug"] == s]
            my_prs = [pr for (ps, _), (_, _, pr) in sorted(prs.items()) if ps == s]
            if not mine and not my_prs:
                continue
            counts = []
            if mine:
                counts.append(f"{len(mine)} commit(s)")
            if my_prs:
                counts.append(f"{len(my_prs)} PR(s)")
            shipped.append(f"- **{_clean(display.get(s, s))}**: " + ", ".join(counts))
            if verbatim:
                shipped += [f"    - #{pr['number']} {_clean(pr.get('title') or '')} "
                            f"({pr.get('state', '')})" for pr in my_prs[:PER_PROJECT]]
                shipped += [f"    - {_clean(c.get('subject') or '')}"
                            for c in mine[:PER_PROJECT]]
        slides.append(shipped)

    if named and runs:
        by_pipeline: dict[str, list[dict]] = {}
        for _, r in runs:
            by_pipeline.setdefault(r["pipeline"], []).append(r)
        lines = ["## Pipelines", "", "| Pipeline | Runs | Succeeded | Median time | Versions |",
                 "|---|---:|---:|---:|---|"]
        for name, rs in sorted(by_pipeline.items(), key=lambda kv: -len(kv[1])):
            mins = [r["minutes"] for r in rs if r.get("minutes") is not None]
            versions = ", ".join(sorted({_clean(r["version"]) for r in rs if r.get("version")}))
            lines.append(f"| {_clean(name)} | {len(rs)} | "
                         f"{sum(r['status'] == 'success' for r in rs)} | "
                         f"{_hours(statistics.median(mins)) if mins else '—'} | {versions} |")
        failures = [r for _, r in runs if r["status"] == "failed" and r.get("error")]
        if verbatim and failures:
            lines += ["", "**Failures**"] + [
                f"- {_clean(r['pipeline'])}: {_clean(r['error'])}" for r in failures[:4]]
        slides.append(lines)

    topics = Counter(t for m in active.values() for t in m.get("topics") or []
                     if verbatim or detector.categories.get(t) != "custom")
    if named and topics:
        slides.append(["## Stack & topics", "", " · ".join(
            f"{_clean(detector.names.get(t, t))} ×{n}" for t, n in topics.most_common(12))])

    def is_open(thread: str) -> bool:
        return thread_key(safe_text(thread)) not in done

    open_threads = [(d, p["slug"], t) for d, p in entries for t in p.get("threads") or []
                    if is_open(t)]
    open_threads += [(d, None, t) for d, m in active.items() for t in m.get("threads") or []
                     if is_open(t)]
    if open_threads:
        seen: set[str] = set()
        unique = []
        for d, s, t in sorted(open_threads, reverse=True):
            if thread_key(t) not in seen:
                seen.add(thread_key(t))
                unique.append((d, s, t))
        nxt = ["## Next steps", ""]
        if verbatim:
            nxt += [f"- {_clean(t)}" for _, _, t in unique[:MAX_THREADS]]
            if len(unique) > MAX_THREADS:
                nxt.append(f"- …and {len(unique) - MAX_THREADS} more open thread(s)")
        elif named:
            per = Counter(s for _, s, _ in unique if s)
            nxt += [f"- **{_clean(display.get(s, s))}**: {n} open thread(s)"
                    for s, n in per.most_common()]
            loose = sum(1 for _, s, _ in unique if s is None)
            if loose:
                nxt.append(f"- {loose} failed pipeline run(s) to follow up")
        else:
            nxt.append(f"- {len(unique)} open thread(s) carried forward")
        slides.append(nxt)

    if verbatim:
        refs = sorted(all_references(active).values(), key=lambda e: e["days"][-1],
                      reverse=True)
        if refs:
            reading = ["## Reading", ""]
            for e in refs[:MAX_REFS]:
                r = as_reference(e["ref"])
                reading.append(f"- [{r.label}]({r.url})")
            slides.append(reading)

    return SLIDE_BREAK.join("\n".join(s).rstrip() for s in slides) + "\n"


def cmd_deck(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="devlog deck",
        description="Slide outline (Markdown, --- between slides) for Gamma, Marp, or PowerPoint",
    )
    period = parser.add_mutually_exclusive_group()
    period.add_argument("--week", nargs="?", const="latest", help="ISO week, e.g. 2026-W39")
    period.add_argument("--month", nargs="?", const="latest", help="e.g. 2026-09")
    period.add_argument("--quarter", nargs="?", const="latest", help="e.g. 2026-Q3")
    parser.add_argument("--since", default=None, help="YYYY-MM-DD (with --until: custom range)")
    parser.add_argument("--until", default=None, help="YYYY-MM-DD")
    parser.add_argument("--project", default=None, help="Only this project")
    parser.add_argument("--detail", choices=PUBLIC_DETAIL_LEVELS, default=None,
                        help="What the deck may show (default: public_detail from config)")
    parser.add_argument("--out", type=Path, default=None, help="Write here instead of stdout")
    parser.add_argument("--config", type=Path, default=None,
                        help="Config path (default: ~/.config/devlog/config.toml)")
    args = parser.parse_args(argv)

    cfg_path = args.config or default_config_path()
    try:
        cfg = load_config(cfg_path) or DevlogConfig()
    except (OSError, ValueError) as exc:
        print(f"Could not load config at {cfg_path}: {exc}")
        return 2
    if vault_root(cfg) is None or not vault_root(cfg).is_dir():
        print("devlog deck reads the Obsidian vault index; set obsidian_vault and run "
              "`devlog obsidian --backfill` first.")
        return 2
    folder_root = _folder_root(cfg)
    days = load_index(folder_root)
    if not days:
        print(f"The vault index at {folder_root} is empty; run `devlog obsidian --backfill`.")
        return 2
    try:
        label, start, end = resolve_scope(days, week=args.week, month=args.month,
                                          quarter=args.quarter, since=args.since,
                                          until=args.until)
    except ValueError as exc:
        print(f"Invalid period: {exc}")
        return 2
    configure_redaction(cfg.redact_patterns, cfg.redact_presets)
    try:
        deck = build_deck(days, label=label, start=start, end=end,
                          detail=args.detail or cfg.public_detail, project=args.project,
                          detector=TopicDetector(cfg.topics),
                          done=load_state(folder_root)["done_threads"],
                          prices=price_table(cfg.model_prices))
    except ValueError as exc:
        print(str(exc))
        return 2
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(deck, encoding="utf-8")
        slides = deck.count("\n---\n") + 1
        print(f"Wrote {slides} slide(s) to {args.out}. In Gamma: Create → Paste in text → "
              "card-by-card (--- separates slides).", file=sys.stderr)
    else:
        sys.stdout.write(deck)
    return 0
