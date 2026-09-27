"""Turn mirrored devlog days into a linked Obsidian knowledge graph.

Layout under `{vault}/{obsidian_folder}/`:

    YYYY-MM-DD.md          day note: properties, prev/next nav, per-project detail
    Projects/<slug>.md     project hub: timeline, work mix, frequent files
    Work/<type>.md         work-type hub: every day of e.g. code-review
    Weekly/YYYY-Www.md     weekly rollup
    DevLog Home.md         dashboard (static tables + optional Dataview)
    .devlog/index.json     per-day metadata; source of truth for regeneration

Every generated note is `frontmatter + %% devlog:start %% ... %% devlog:end %%`
followed by a free-form area. Only the managed block is rewritten; anything
the user writes below `%% devlog:end %%` survives every refresh.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from devlog.knowledge import headline, is_empty_day
from devlog.worktypes import describe

INDEX_VERSION = 1
START = "%% devlog:start %%"
END = "%% devlog:end %%"
DEFAULT_TAIL = "\n\n## Notes\n\n"
HOME_NAME = "DevLog Home"
PROJECTS_DIR = "Projects"
WORK_DIR = "Work"
WEEKLY_DIR = "Weekly"
RECENT_DAYS = 14
_DAY_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


# ---------------------------------------------------------------- index I/O


def index_path(folder_root: Path) -> Path:
    return folder_root / ".devlog" / "index.json"


def load_index(folder_root: Path) -> dict[str, dict]:
    path = index_path(folder_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    days = data.get("days") if isinstance(data, dict) else None
    if not isinstance(days, dict):
        return {}
    return {k: v for k, v in days.items() if isinstance(v, dict)}


def save_index(folder_root: Path, days: dict[str, dict]) -> None:
    path = index_path(folder_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": INDEX_VERSION, "days": dict(sorted(days.items()))}
    _write_if_changed(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- text helpers


def _write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def _tail(path: Path, legacy_generated: str | None = None) -> str:
    """User-owned text after the managed block (default Notes heading if none).

    A pre-graph day note has no END marker. If its body differs from what the
    old mirror generated (`legacy_generated`), the user edited it: keep the
    whole old body under Notes rather than silently dropping it.
    """
    if not path.exists():
        return DEFAULT_TAIL
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    idx = text.find(END)
    if idx != -1:
        return text[idx + len(END) :] or DEFAULT_TAIL
    body = _FRONTMATTER_RE.sub("", text, count=1).strip()
    if not body or legacy_generated is None or body == legacy_generated.strip():
        return DEFAULT_TAIL
    return f"{DEFAULT_TAIL}### Kept from the previous version of this note\n\n{body}\n"


def _is_untouched(path: Path) -> bool:
    return _tail(path).strip() in {"", "## Notes"}


def _compose(
    frontmatter: list[str], body: list[str], path: Path, legacy_generated: str | None = None
) -> str:
    fm = "---\n" + "\n".join(frontmatter) + "\n---\n"
    managed = START + "\n" + "\n".join(body).strip("\n") + "\n" + END
    return fm + managed + _tail(path, legacy_generated)


def safe_text(text: str, *, table: bool = False) -> str:
    """Neutralize markdown that would create links, tags, comments, or HTML."""
    text = re.sub(r"\s+", " ", text or "").strip()
    text = text.replace("%%", "% %").replace("[[", "[ [").replace("]]", "] ]")
    text = text.replace("<", "&lt;")
    text = re.sub(r"#(?=\S)", r"\\#", text)
    if table:
        text = text.replace("|", "\\|")
    return text


def _q(value: str) -> str:
    """YAML double-quoted scalar (JSON strings are valid YAML)."""
    return json.dumps(value, ensure_ascii=False)


def _tag(slug: str) -> str:
    return re.sub(r"[^a-z0-9_/-]+", "-", slug.lower()).strip("-")


def _yaml_list(key: str, items: list[str], *, quote: bool = True) -> list[str]:
    if not items:
        return [f"{key}: []"]
    return [f"{key}:"] + [f"  - {_q(i) if quote else i}" for i in items]


def _minutes(value) -> str:
    return "—" if value is None else f"{int(value):,}"


def _sum_minutes(values: list) -> str:
    """Total that admits unknowns: 'N', 'N+' (some unknown), or '—' (all unknown)."""
    known = [int(v) for v in values if v is not None]
    if not known:
        return "—"
    return f"{sum(known):,}" + ("+" if len(known) < len(values) else "")


class Graph:
    """Path and link conventions for one vault folder."""

    def __init__(self, vault: Path, folder: str) -> None:
        self.vault = vault
        self.folder = folder.strip().strip("/\\")
        self.root = vault / self.folder if self.folder else vault

    def _rel(self, *parts: str) -> str:
        return "/".join(p for p in (self.folder, *parts) if p)

    def link(self, rel: str, alias: str, *, table: bool = False) -> str:
        sep = "\\|" if table else "|"
        return f"[[{rel}{sep}{alias}]]"

    def day_rel(self, day: str) -> str:
        return self._rel(day)

    def project_rel(self, slug: str) -> str:
        return self._rel(PROJECTS_DIR, slug)

    def work_rel(self, slug: str) -> str:
        return self._rel(WORK_DIR, slug)

    def week_rel(self, week: str) -> str:
        return self._rel(WEEKLY_DIR, week)

    def home_rel(self) -> str:
        return self._rel(HOME_NAME)

    def path(self, rel: str) -> Path:
        return self.vault / f"{rel}.md"

    def day(self, d: str, *, table: bool = False) -> str:
        return self.link(self.day_rel(d), d, table=table)

    def project(self, slug: str, name: str | None = None, *, table: bool = False) -> str:
        return self.link(self.project_rel(slug), name or slug, table=table)

    def work(self, slug: str, *, table: bool = False) -> str:
        return self.link(self.work_rel(slug), slug, table=table)

    def week(self, week: str, *, table: bool = False) -> str:
        return self.link(self.week_rel(week), week, table=table)


def iso_week(day: str) -> str:
    year, week, _ = date.fromisoformat(day).isocalendar()
    return f"{year}-W{week:02d}"


def _project_names(days: dict[str, dict]) -> dict[str, Counter]:
    names: dict[str, Counter] = {}
    for meta in days.values():
        for p in meta.get("projects") or []:
            names.setdefault(p["slug"], Counter())[p.get("name") or p["slug"]] += 1
    return names


# ---------------------------------------------------------------- renderers


def render_day(g: Graph, meta: dict, prev_day: str | None, next_day: str | None,
               display: dict[str, str]) -> str:
    day = meta["date"]
    week = iso_week(day)
    projects = meta.get("projects") or []
    work = meta.get("work_types") or []
    fm = [
        f"date: {day}",
        "type: devlog-day",
        f"week: {_q(g.week(week))}",
        f"active_minutes: {int(meta.get('active_minutes') or 0)}",
    ]
    if meta.get("sessions") is not None:
        fm.append(f"sessions: {int(meta['sessions'])}")
    fm += _yaml_list("projects", [g.project(p["slug"], display.get(p["slug"])) for p in projects])
    fm += _yaml_list("work_types", [g.work(w) for w in work])
    if meta.get("sources"):
        fm += _yaml_list("sources", meta["sources"], quote=False)
    tags = ["devlog"] + [f"devlog/project/{_tag(p['slug'])}" for p in projects]
    tags += [f"devlog/work/{_tag(w)}" for w in work]
    fm += _yaml_list("tags", tags, quote=False)

    nav = [f"← {g.day(prev_day)}" if prev_day else "← (first)", g.week(week),
           g.link(g.home_rel(), "Home"), f"{g.day(next_day)} →" if next_day else "(latest) →"]
    body = [" · ".join(nav), "", f"# {day}", "", (meta.get("summary") or "").strip(), ""]

    if projects:
        body.append("## Projects")
        for p in projects:
            body.append("")
            body.append(f"### {g.project(p['slug'], display.get(p['slug']))} · "
                        f"{_minutes(p.get('minutes'))} min")
            if p.get("work_types"):
                body.append("- **Work:** " + ", ".join(g.work(w) for w in p["work_types"]))
            if p.get("sources"):
                body.append("- **Sources:** " + ", ".join(p["sources"]))
            if p.get("tasks"):
                body.append("- **Asked for:**")
                body += [f"    - {safe_text(t)}" for t in p["tasks"]]
            if p.get("files"):
                body.append("- **Files:** " + ", ".join(f"`{f.replace('`', '')}`"
                                                        for f in p["files"]))
            if p.get("tools"):
                body.append("- **Tools:** " + ", ".join(
                    f"{safe_text(k)} ×{v}" for k, v in p["tools"].items()))
    legacy = f"# {day}\n\n{(meta.get('summary') or '').strip()}"
    return _compose(fm, body, g.path(g.day_rel(day)), legacy)


def _timeline_rows(g: Graph, entries: list[tuple[str, dict, dict | None]]) -> list[str]:
    rows = ["| Day | Min | Work | Focus |", "|---|---:|---|---|"]
    for day, meta, project in entries:
        source = project if project is not None else meta
        mins = source.get("minutes") if project is not None else meta.get("active_minutes")
        work = ", ".join(g.work(w, table=True) for w in source.get("work_types") or [])
        focus = headline({"projects": [project]} if project else meta)
        rows.append(f"| {g.day(day, table=True)} | {_minutes(mins)} | {work} | "
                    f"{safe_text(focus, table=True)} |")
    return rows


def render_project(g: Graph, slug: str, days: dict[str, dict], names: Counter) -> str:
    entries = []
    for day in sorted(days, reverse=True):
        for p in days[day].get("projects") or []:
            if p["slug"] == slug:
                entries.append((day, days[day], p))
    display = names.most_common(1)[0][0]
    minutes = [p.get("minutes") for _, _, p in entries]
    total = sum(m for m in minutes if m is not None)
    work = Counter(w for _, _, p in entries for w in p.get("work_types") or [])
    sources = sorted({s for _, _, p in entries for s in p.get("sources") or []})
    files = Counter(f for _, _, p in entries for f in p.get("files") or [])
    first, last = entries[-1][0], entries[0][0]
    aliases = sorted({n for n in names if n != slug})

    fm = ["type: devlog-project", f"project: {_q(slug)}"]
    fm += _yaml_list("aliases", aliases)
    fm += [f"active_days: {len(entries)}", f"active_minutes: {total}",
           f"first_active: {first}", f"last_active: {last}"]
    fm += _yaml_list("work_types", [g.work(w) for w, _ in work.most_common()])
    fm += _yaml_list("tags", ["devlog", "devlog/project-hub",
                              f"devlog/project/{_tag(slug)}"], quote=False)

    body = [
        f"# {display}", "",
        "> [!summary] Activity",
        f"> **{len(entries)}** active day(s) · **{_sum_minutes(minutes)}** active min · "
        f"first {g.day(first)} · last {g.day(last)}", "",
    ]
    if work:
        body.append("**Work mix:** " + " · ".join(f"{g.work(w)} ×{n}"
                                                   for w, n in work.most_common()))
    if sources:
        body.append("**Sources:** " + ", ".join(sources))
    if files:
        body.append("**Frequently touched files:** " + ", ".join(
            f"`{f.replace('`', '')}` ({n})" for f, n in files.most_common(10)))
    body += ["", "## Timeline", ""] + _timeline_rows(g, entries)
    return _compose(fm, body, g.path(g.project_rel(slug)))


def render_work(g: Graph, slug: str, days: dict[str, dict], display: dict[str, str]) -> str:
    matching = sorted((d for d, m in days.items() if slug in (m.get("work_types") or [])),
                      reverse=True)
    projects = Counter(
        p["slug"] for d in matching for p in days[d].get("projects") or []
        if slug in (p.get("work_types") or [])
    )
    fm = ["type: devlog-work-type", f"work_type: {_q(slug)}",
          f"active_days: {len(matching)}"]
    fm += _yaml_list("projects", [g.project(p, display.get(p)) for p, _ in projects.most_common()])
    fm += _yaml_list("tags", ["devlog", "devlog/work-hub", f"devlog/work/{_tag(slug)}"],
                     quote=False)
    body = [f"# {slug}", "", f"> [!abstract] {describe(slug) or 'Work type'}",
            f"> **{len(matching)}** day(s)" + (f" across {len(projects)} project(s)"
                                               if projects else ""), ""]
    if projects:
        body.append("**Projects:** " + " · ".join(
            f"{g.project(p, display.get(p))} ×{n}" for p, n in projects.most_common()))
        body.append("")
    body += ["## Days", "", "| Day | Projects | Focus |", "|---|---|---|"]
    for d in matching:
        meta = days[d]
        hits = [p for p in meta.get("projects") or [] if slug in (p.get("work_types") or [])]
        names = ", ".join(g.project(p["slug"], display.get(p["slug"]), table=True)
                          for p in (hits or meta.get("projects") or []))
        focus = headline({"projects": hits} if hits else meta)
        body.append(f"| {g.day(d, table=True)} | {names} | {safe_text(focus, table=True)} |")
    return _compose(fm, body, g.path(g.work_rel(slug)))


def render_week(g: Graph, week: str, weeks: list[str], days: dict[str, dict],
                display: dict[str, str]) -> str:
    year, num = week.split("-W")
    monday = date.fromisocalendar(int(year), int(num), 1)
    sunday = monday + timedelta(days=6)
    in_week = sorted(d for d in days if iso_week(d) == week)
    active = [d for d in in_week if not is_empty_day(days[d])]
    minutes = sum(int(days[d].get("active_minutes") or 0) for d in in_week)
    per_project: dict[str, list] = {}
    project_days: Counter = Counter()
    work: Counter = Counter()
    for d in active:
        for p in days[d].get("projects") or []:
            per_project.setdefault(p["slug"], []).append(p.get("minutes"))
            project_days[p["slug"]] += 1
        work.update(days[d].get("work_types") or [])

    i = weeks.index(week)
    prev_w = weeks[i - 1] if i > 0 else None
    next_w = weeks[i + 1] if i + 1 < len(weeks) else None
    fm = ["type: devlog-week", f"week: {_q(week)}", f"start: {monday}", f"end: {sunday}",
          f"active_days: {len(active)}", f"active_minutes: {minutes}"]
    fm += _yaml_list("projects", [g.project(p, display.get(p)) for p in sorted(project_days)])
    fm += _yaml_list("tags", ["devlog", "devlog/week"], quote=False)

    nav = [f"← {g.week(prev_w)}" if prev_w else "← (first)", g.link(g.home_rel(), "Home"),
           f"{g.week(next_w)} →" if next_w else "(latest) →"]
    body = [" · ".join(nav), "", f"# {week}", "",
            f"{monday:%b %d} – {sunday:%b %d, %Y} · **{len(active)}** active day(s) · "
            f"**{minutes:,}** active min", ""]
    if project_days:
        body += ["## Projects", "", "| Project | Days | Min |", "|---|---:|---:|"]
        for slug, n in project_days.most_common():
            body.append(f"| {g.project(slug, display.get(slug), table=True)} | {n} | "
                        f"{_sum_minutes(per_project[slug])} |")
        body.append("")
    if work:
        body.append("**Work mix:** " + " · ".join(f"{g.work(w)} ×{n}"
                                                   for w, n in work.most_common()))
        body.append("")
    body.append("## Days")
    body.append("")
    for d in in_week:
        meta = days[d]
        if is_empty_day(meta):
            body.append(f"- {g.day(d)} · quiet day")
            continue
        projs = ", ".join(g.project(p["slug"], display.get(p["slug"]))
                          for p in meta.get("projects") or [])
        focus = safe_text(headline(meta))
        line = f"- {g.day(d)} · {_minutes(meta.get('active_minutes'))} min"
        line += f" · {projs}" if projs else ""
        line += f" — {focus}" if focus else ""
        body.append(line)
    return _compose(fm, body, g.path(g.week_rel(week)))


def render_home(g: Graph, days: dict[str, dict], display: dict[str, str]) -> str:
    active = sorted((d for d in days if not is_empty_day(days[d])), reverse=True)
    total = sum(int(days[d].get("active_minutes") or 0) for d in days)
    proj_days: Counter = Counter()
    proj_min: dict[str, list] = {}
    proj_last: dict[str, str] = {}
    work: Counter = Counter()
    for d in active:
        for p in days[d].get("projects") or []:
            proj_days[p["slug"]] += 1
            proj_min.setdefault(p["slug"], []).append(p.get("minutes"))
            proj_last.setdefault(p["slug"], d)
        work.update(days[d].get("work_types") or [])
    weeks = sorted({iso_week(d) for d in days}, reverse=True)

    fm = ["type: devlog-home", f"active_days: {len(active)}", f"active_minutes: {total}"]
    fm += _yaml_list("tags", ["devlog", "devlog/home"], quote=False)
    body = [f"# {HOME_NAME}", "",
            f"**{len(active)}** active day(s) · **{total:,}** active min · "
            f"**{len(proj_days)}** project(s)"
            + (f" · {g.day(active[-1])} → {g.day(active[0])}" if active else ""), ""]
    if proj_days:
        body += ["## Projects", "", "| Project | Days | Min | Last active |", "|---|---:|---:|---|"]
        for slug in sorted(proj_days, key=lambda s: (proj_last[s], proj_days[s]), reverse=True):
            body.append(f"| {g.project(slug, display.get(slug), table=True)} | {proj_days[slug]} | "
                        f"{_sum_minutes(proj_min[slug])} | {g.day(proj_last[slug], table=True)} |")
        body.append("")
    if work:
        body += ["## Work types", "",
                 " · ".join(f"{g.work(w)} ×{n}" for w, n in work.most_common()), ""]
    if active:
        body += ["## Recent days", ""]
        for d in active[:RECENT_DAYS]:
            focus = safe_text(headline(days[d]))
            projs = ", ".join(g.project(p["slug"], display.get(p["slug"]))
                              for p in days[d].get("projects") or [])
            line = f"- {g.day(d)} · {_minutes(days[d].get('active_minutes'))} min"
            line += f" · {projs}" if projs else ""
            line += f" — {focus}" if focus else ""
            body.append(line)
        body.append("")
    if weeks:
        body += ["## Weeks", "", " · ".join(g.week(w) for w in weeks), ""]
    folder = g.folder or "/"
    body += [
        "> [!tip]- Live queries (needs the Dataview community plugin)",
        "> ```dataview",
        "> TABLE active_minutes AS \"Min\", projects AS \"Projects\", work_types AS \"Work\"",
        f"> FROM \"{folder}\" WHERE type = \"devlog-day\" AND active_minutes > 0",
        "> SORT date DESC",
        "> LIMIT 30",
        "> ```",
        "> ```dataview",
        "> TABLE active_days AS \"Days\", active_minutes AS \"Min\", last_active AS \"Last\"",
        f"> FROM \"{folder}\" WHERE type = \"devlog-project\"",
        "> SORT last_active DESC",
        "> ```",
    ]
    return _compose(fm, body, g.path(g.home_rel()))


# ---------------------------------------------------------------- refresh


def _neighbors(days: dict[str, dict]) -> dict[str, tuple[str | None, str | None]]:
    """Prev/next *active* day for every day, so nav skips quiet days."""
    ordered = sorted(days)
    active = [d for d in ordered if not is_empty_day(days[d])]
    out: dict[str, tuple[str | None, str | None]] = {}
    for d in ordered:
        prev_d = next((a for a in reversed(active) if a < d), None)
        next_d = next((a for a in active if a > d), None)
        out[d] = (prev_d, next_d)
    return out


def _prune_stale(directory: Path, keep: set[str]) -> list[Path]:
    removed: list[Path] = []
    if not directory.is_dir():
        return removed
    for path in directory.glob("*.md"):
        if path.stem in keep:
            continue
        text = path.read_text(encoding="utf-8")
        if END in text and _is_untouched(path):
            path.unlink()
            removed.append(path)
    return removed


def refresh_graph(vault: Path, folder: str, days: dict[str, dict]) -> dict:
    """Regenerate every managed note from `days`. Writes only changed files."""
    g = Graph(vault, folder)
    names = _project_names(days)
    display = {slug: c.most_common(1)[0][0] for slug, c in names.items()}
    written: list[Path] = []

    def emit(rel: str, text: str) -> None:
        path = g.path(rel)
        if _write_if_changed(path, text):
            written.append(path)

    neighbors = _neighbors(days)
    for d, meta in days.items():
        prev_d, next_d = neighbors[d]
        emit(g.day_rel(d), render_day(g, meta, prev_d, next_d, display))

    active_days = {d: m for d, m in days.items() if not is_empty_day(m)}
    for slug, counter in names.items():
        emit(g.project_rel(slug), render_project(g, slug, active_days, counter))
    work_types = {w for m in active_days.values() for w in m.get("work_types") or []}
    for slug in work_types:
        emit(g.work_rel(slug), render_work(g, slug, active_days, display))
    weeks = sorted({iso_week(d) for d in days})
    for week in weeks:
        emit(g.week_rel(week), render_week(g, week, weeks, days, display))
    if days:
        emit(g.home_rel(), render_home(g, days, display))

    removed = _prune_stale(g.root / PROJECTS_DIR, set(names))
    removed += _prune_stale(g.root / WORK_DIR, work_types)
    removed += _prune_stale(g.root / WEEKLY_DIR, set(weeks))
    return {"written": [str(p) for p in written], "removed": [str(p) for p in removed]}


def existing_day_notes(folder_root: Path) -> set[str]:
    if not folder_root.is_dir():
        return set()
    return {p.stem for p in folder_root.iterdir() if _DAY_FILE_RE.match(p.name)}
