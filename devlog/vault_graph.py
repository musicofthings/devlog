"""Turn mirrored devlog days into a linked Obsidian knowledge graph.

Layout under `{vault}/{obsidian_folder}/`:

    YYYY-MM-DD.md          day note: properties, nav, per-project detail,
                           commits, tokens, open threads (checkboxes)
    Projects/<slug>.md     project hub: repo, open threads, timeline, work mix
    Work/<type>.md         work-type hub: every day of e.g. code-review
    Weekly/YYYY-Www.md     weekly rollup
    Monthly/YYYY-MM.md     monthly rollup
    DevLog Home.md         dashboard (static tables, Bases view, optional Dataview)
    DevLog.base            Obsidian Bases views (written once; yours to edit)
    .devlog/index.json     per-day metadata + ticked threads; regeneration source

Every generated note is `frontmatter + %% devlog:start %% ... %% devlog:end %%`
followed by a free-form area. Only the managed block is rewritten; anything
the user writes below `%% devlog:end %%` survives every refresh. Ticking an
open-thread checkbox (in a day note or a project hub) is remembered.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from devlog.knowledge import headline, is_empty_day
from devlog.worktypes import describe

INDEX_VERSION = 2
START = "%% devlog:start %%"
END = "%% devlog:end %%"
DEFAULT_TAIL = "\n\n## Notes\n\n"
HOME_NAME = "DevLog Home"
BASE_NAME = "DevLog"
PROJECTS_DIR = "Projects"
WORK_DIR = "Work"
WEEKLY_DIR = "Weekly"
MONTHLY_DIR = "Monthly"
RECENT_DAYS = 14
MAX_HUB_THREADS = 30
_DAY_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
_CHECKBOX_RE = re.compile(r"^\s*[-*+] \[(?P<mark>[ xX])\] (?P<text>.+)$")
_THREAD_SUFFIX_RE = re.compile(r"(?:\s+✅\s*\d{4}-\d{2}-\d{2}|\s+·\s+\[\[[^\]]*\]\])+\s*$")


# ---------------------------------------------------------------- index I/O


def index_path(folder_root: Path) -> Path:
    return folder_root / ".devlog" / "index.json"


def _read_index(folder_root: Path) -> dict:
    try:
        data = json.loads(index_path(folder_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_index(folder_root: Path) -> dict[str, dict]:
    days = _read_index(folder_root).get("days")
    if not isinstance(days, dict):
        return {}
    return {k: v for k, v in days.items() if isinstance(v, dict)}


def load_done_threads(folder_root: Path) -> set[str]:
    done = _read_index(folder_root).get("done_threads")
    return {d for d in done if isinstance(d, str)} if isinstance(done, list) else set()


def save_index(
    folder_root: Path, days: dict[str, dict], done_threads: set[str] | None = None
) -> None:
    if done_threads is None:
        done_threads = load_done_threads(folder_root)
    path = index_path(folder_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": INDEX_VERSION,
        "days": dict(sorted(days.items())),
        "done_threads": sorted(done_threads),
    }
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


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _token_line(tokens: dict | None) -> str:
    if not tokens or not any(tokens.values()):
        return ""
    return (f"{_fmt_tokens(tokens.get('in', 0))} in · {_fmt_tokens(tokens.get('out', 0))} out"
            f" · {_fmt_tokens(tokens.get('cache', 0))} cached")


def _add_tokens(total: dict[str, int], tokens: dict | None) -> None:
    for key in ("in", "out", "cache"):
        total[key] = total.get(key, 0) + int((tokens or {}).get(key) or 0)


def thread_key(text: str) -> str:
    """Identity of an open thread across notes: rendered text, case/space-folded."""
    text = _THREAD_SUFFIX_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip().lower()


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

    def period_rel(self, kind: str, label: str) -> str:
        return self._rel(WEEKLY_DIR if kind == "week" else MONTHLY_DIR, label)

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
        return self.link(self.period_rel("week", week), week, table=table)

    def month(self, month: str, *, table: bool = False) -> str:
        return self.link(self.period_rel("month", month), month, table=table)

    def home(self) -> str:
        return self.link(self.home_rel(), "Home")


def iso_week(day: str) -> str:
    year, week, _ = date.fromisoformat(day).isocalendar()
    return f"{year}-W{week:02d}"


def month_of(day: str) -> str:
    return day[:7]


def _project_names(days: dict[str, dict]) -> dict[str, Counter]:
    names: dict[str, Counter] = {}
    for meta in days.values():
        for p in meta.get("projects") or []:
            names.setdefault(p["slug"], Counter())[p.get("name") or p["slug"]] += 1
    return names


def _checkbox(text: str, done: set[str], suffix: str = "") -> str:
    shown = safe_text(text)
    mark = "x" if thread_key(shown) in done else " "
    return f"- [{mark}] {shown}{suffix}"


# ---------------------------------------------------------------- renderers


def render_day(g: Graph, meta: dict, prev_day: str | None, next_day: str | None,
               display: dict[str, str], done: set[str]) -> str:
    day = meta["date"]
    week, month = iso_week(day), month_of(day)
    projects = meta.get("projects") or []
    work = meta.get("work_types") or []
    fm = [
        f"date: {day}",
        "type: devlog-day",
        f"week: {_q(g.week(week))}",
        f"month: {_q(g.month(month))}",
        f"active_minutes: {int(meta.get('active_minutes') or 0)}",
    ]
    if meta.get("sessions") is not None:
        fm.append(f"sessions: {int(meta['sessions'])}")
    tokens = meta.get("tokens") or {}
    if any(tokens.values()):
        fm += [f"tokens_in: {int(tokens.get('in', 0))}",
               f"tokens_out: {int(tokens.get('out', 0))}"]
    commits = sum(len(p.get("commits") or []) for p in projects)
    if commits:
        fm.append(f"commits: {commits}")
    open_threads = [t for p in projects for t in p.get("threads") or []
                    if thread_key(safe_text(t)) not in done]
    if open_threads:
        fm.append(f"open_threads: {len(open_threads)}")
    fm += _yaml_list("projects", [g.project(p["slug"], display.get(p["slug"])) for p in projects])
    fm += _yaml_list("work_types", [g.work(w) for w in work])
    if meta.get("sources"):
        fm += _yaml_list("sources", meta["sources"], quote=False)
    tags = ["devlog"] + [f"devlog/project/{_tag(p['slug'])}" for p in projects]
    tags += [f"devlog/work/{_tag(w)}" for w in work]
    fm += _yaml_list("tags", tags, quote=False)

    nav = [f"← {g.day(prev_day)}" if prev_day else "← (first)", g.week(week), g.month(month),
           g.home(), f"{g.day(next_day)} →" if next_day else "(latest) →"]
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
            if p.get("commits"):
                body.append("- **Commits:**")
                for c in p["commits"]:
                    sha = f"[`{c['short']}`]({c['url']})" if c.get("url") else f"`{c['short']}`"
                    body.append(f"    - {sha} {safe_text(c.get('subject') or '')}")
            if p.get("files"):
                body.append("- **Files:** " + ", ".join(f"`{f.replace('`', '')}`"
                                                        for f in p["files"]))
            if p.get("tools"):
                body.append("- **Tools:** " + ", ".join(
                    f"{safe_text(k)} ×{v}" for k, v in p["tools"].items()))
            token_line = _token_line(p.get("tokens"))
            if token_line:
                body.append(f"- **Tokens:** {token_line}")
            if p.get("threads"):
                body.append("- **Open threads** (from the session recap):")
                body += ["    " + _checkbox(t, done) for t in p["threads"]]
    legacy = f"# {day}\n\n{(meta.get('summary') or '').strip()}"
    return _compose(fm, body, g.path(g.day_rel(day)), legacy)


def _timeline_rows(g: Graph, entries: list[tuple[str, dict, dict]]) -> list[str]:
    rows = ["| Day | Min | Commits | Work | Focus |", "|---|---:|---:|---|---|"]
    for day, _meta, project in entries:
        work = ", ".join(g.work(w, table=True) for w in project.get("work_types") or [])
        focus = headline({"projects": [project]})
        commits = len(project.get("commits") or [])
        rows.append(f"| {g.day(day, table=True)} | {_minutes(project.get('minutes'))} | "
                    f"{commits or ''} | {work} | {safe_text(focus, table=True)} |")
    return rows


def project_open_threads(slug: str, days: dict[str, dict], done: set[str]) -> list[tuple[str, str]]:
    """(thread, day) pairs still open for a project, newest first, deduplicated."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for day in sorted(days, reverse=True):
        for p in days[day].get("projects") or []:
            if p["slug"] != slug:
                continue
            for thread in p.get("threads") or []:
                key = thread_key(safe_text(thread))
                if key in done or key in seen:
                    continue
                seen.add(key)
                out.append((thread, day))
    return out


def render_project(g: Graph, slug: str, days: dict[str, dict], names: Counter,
                   done: set[str]) -> str:
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
    commits = sum(len(p.get("commits") or []) for _, _, p in entries)
    tokens: dict[str, int] = {}
    for _, _, p in entries:
        _add_tokens(tokens, p.get("tokens"))
    repo = next((p["repo_url"] for _, _, p in entries if p.get("repo_url")), None)
    threads = project_open_threads(slug, days, done)
    first, last = entries[-1][0], entries[0][0]
    aliases = sorted({n for n in names if n != slug})

    fm = ["type: devlog-project", f"project: {_q(slug)}"]
    if repo:
        fm.append(f"repo: {_q(repo)}")
    fm += _yaml_list("aliases", aliases)
    fm += [f"active_days: {len(entries)}", f"active_minutes: {total}",
           f"first_active: {first}", f"last_active: {last}",
           f"open_threads: {len(threads)}"]
    if commits:
        fm.append(f"commits: {commits}")
    if any(tokens.values()):
        fm += [f"tokens_in: {tokens['in']}", f"tokens_out: {tokens['out']}"]
    fm += _yaml_list("work_types", [g.work(w) for w, _ in work.most_common()])
    fm += _yaml_list("tags", ["devlog", "devlog/project-hub",
                              f"devlog/project/{_tag(slug)}"], quote=False)

    summary = (f"> **{len(entries)}** active day(s) · **{_sum_minutes(minutes)}** active min · "
               f"first {g.day(first)} · last {g.day(last)}")
    if commits:
        summary += f" · **{commits}** commit(s)"
    body = [f"# {display}", "", "> [!summary] Activity", summary]
    token_line = _token_line(tokens)
    if token_line:
        body.append(f"> Tokens: {token_line}")
    body.append("")
    if repo:
        body.append(f"**Repo:** [{repo.removeprefix('https://')}]({repo})")
    if work:
        body.append("**Work mix:** " + " · ".join(f"{g.work(w)} ×{n}"
                                                   for w, n in work.most_common()))
    if sources:
        body.append("**Sources:** " + ", ".join(sources))
    if files:
        body.append("**Frequently touched files:** " + ", ".join(
            f"`{f.replace('`', '')}` ({n})" for f, n in files.most_common(10)))
    if threads:
        body += ["", "## Open threads", ""]
        body += [_checkbox(t, done, f" · {g.day(d)}") for t, d in threads[:MAX_HUB_THREADS]]
        if len(threads) > MAX_HUB_THREADS:
            body.append(f"- … {len(threads) - MAX_HUB_THREADS} older thread(s) in day notes")
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


def _period_bounds(kind: str, label: str) -> tuple[date, date]:
    if kind == "week":
        year, num = label.split("-W")
        start = date.fromisocalendar(int(year), int(num), 1)
        return start, start + timedelta(days=6)
    start = date.fromisoformat(f"{label}-01")
    nxt = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


def render_period(g: Graph, kind: str, label: str, labels: list[str], days: dict[str, dict],
                  display: dict[str, str]) -> str:
    """Weekly or monthly rollup."""
    start, end = _period_bounds(kind, label)
    key = iso_week if kind == "week" else month_of
    link = g.week if kind == "week" else g.month
    in_period = sorted(d for d in days if key(d) == label)
    active = [d for d in in_period if not is_empty_day(days[d])]
    minutes = sum(int(days[d].get("active_minutes") or 0) for d in in_period)
    per_project: dict[str, list] = {}
    project_days: Counter = Counter()
    project_commits: Counter = Counter()
    work: Counter = Counter()
    tokens: dict[str, int] = {}
    for d in active:
        _add_tokens(tokens, days[d].get("tokens"))
        for p in days[d].get("projects") or []:
            per_project.setdefault(p["slug"], []).append(p.get("minutes"))
            project_days[p["slug"]] += 1
            project_commits[p["slug"]] += len(p.get("commits") or [])
        work.update(days[d].get("work_types") or [])

    i = labels.index(label)
    prev_l = labels[i - 1] if i > 0 else None
    next_l = labels[i + 1] if i + 1 < len(labels) else None
    fm = [f"type: devlog-{kind}", f"{kind}: {_q(label)}", f"start: {start}", f"end: {end}",
          f"active_days: {len(active)}", f"active_minutes: {minutes}"]
    if sum(project_commits.values()):
        fm.append(f"commits: {sum(project_commits.values())}")
    fm += _yaml_list("projects", [g.project(p, display.get(p)) for p in sorted(project_days)])
    fm += _yaml_list("tags", ["devlog", f"devlog/{kind}"], quote=False)

    nav = [f"← {link(prev_l)}" if prev_l else "← (first)", g.home(),
           f"{link(next_l)} →" if next_l else "(latest) →"]
    if kind == "week":
        months = sorted({month_of(start.isoformat()), month_of(end.isoformat())})
        nav.insert(1, " / ".join(g.month(m) for m in months))
        title, span = label, f"{start:%b %d} – {end:%b %d, %Y}"
    else:
        title, span = f"{start:%B %Y}", f"{start:%b %d} – {end:%b %d, %Y}"
    stats = f"{span} · **{len(active)}** active day(s) · **{minutes:,}** active min"
    token_line = _token_line(tokens)
    body = [" · ".join(nav), "", f"# {title}", "", stats]
    if token_line:
        body += ["", f"Tokens: {token_line}"]
    body.append("")
    if project_days:
        body += ["## Projects", "", "| Project | Days | Min | Commits |", "|---|---:|---:|---:|"]
        for slug, n in project_days.most_common():
            body.append(f"| {g.project(slug, display.get(slug), table=True)} | {n} | "
                        f"{_sum_minutes(per_project[slug])} | {project_commits[slug] or ''} |")
        body.append("")
    if work:
        body.append("**Work mix:** " + " · ".join(f"{g.work(w)} ×{n}"
                                                   for w, n in work.most_common()))
        body.append("")
    if kind == "month":
        weeks = sorted({iso_week(d) for d in in_period})
        body += ["**Weeks:** " + " · ".join(g.week(w) for w in weeks), ""]
    body += ["## Days", ""]
    for d in in_period:
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
    return _compose(fm, body, g.path(g.period_rel(kind, label)))


def render_home(g: Graph, days: dict[str, dict], display: dict[str, str],
                done: set[str]) -> str:
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
    months = sorted({month_of(d) for d in days}, reverse=True)
    open_by_project = {s: len(project_open_threads(s, days, done)) for s in proj_days}

    fm = ["type: devlog-home", f"active_days: {len(active)}", f"active_minutes: {total}",
          f"open_threads: {sum(open_by_project.values())}"]
    fm += _yaml_list("tags", ["devlog", "devlog/home"], quote=False)
    body = [f"# {HOME_NAME}", "",
            f"**{len(active)}** active day(s) · **{total:,}** active min · "
            f"**{len(proj_days)}** project(s) · **{sum(open_by_project.values())}** open thread(s)"
            + (f" · {g.day(active[-1])} → {g.day(active[0])}" if active else ""), ""]
    if proj_days:
        body += ["## Projects", "", "| Project | Days | Min | Open threads | Last active |",
                 "|---|---:|---:|---:|---|"]
        for slug in sorted(proj_days, key=lambda s: (proj_last[s], proj_days[s]), reverse=True):
            body.append(f"| {g.project(slug, display.get(slug), table=True)} | {proj_days[slug]} | "
                        f"{_sum_minutes(proj_min[slug])} | {open_by_project[slug] or ''} | "
                        f"{g.day(proj_last[slug], table=True)} |")
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
    if months:
        body += ["## Months", "", " · ".join(g.month(m) for m in months), ""]
    if weeks:
        body += ["## Weeks", "", " · ".join(g.week(w) for w in weeks), ""]
    folder = g.folder or "/"
    base = "/".join(p for p in (g.folder, f"{BASE_NAME}.base") if p)
    body += [
        "## Views",
        "",
        "> [!example]- Obsidian Bases (core plugin, Obsidian 1.9+)",
        f"> ![[{base}]]",
        "",
        "> [!tip]- Live queries (needs the Dataview community plugin)",
        "> ```dataview",
        "> TABLE active_minutes AS \"Min\", projects AS \"Projects\", work_types AS \"Work\"",
        f"> FROM \"{folder}\" WHERE type = \"devlog-day\" AND active_minutes > 0",
        "> SORT date DESC",
        "> LIMIT 30",
        "> ```",
        "> ```dataview",
        "> TABLE active_days AS \"Days\", active_minutes AS \"Min\", open_threads AS \"Open\", "
        "last_active AS \"Last\"",
        f"> FROM \"{folder}\" WHERE type = \"devlog-project\"",
        "> SORT last_active DESC",
        "> ```",
    ]
    return _compose(fm, body, g.path(g.home_rel()))


def render_base(folder: str) -> str:
    """Obsidian Bases views over the generated notes' properties."""
    scope = f'filters:\n  and:\n    - file.inFolder("{folder}")\n' if folder else ""
    return (
        f"{scope}"
        "views:\n"
        "  - type: table\n"
        "    name: Days\n"
        "    filters:\n"
        "      and:\n"
        '        - type == "devlog-day"\n'
        "        - active_minutes > 0\n"
        "    order:\n"
        "      - file.name\n"
        "      - active_minutes\n"
        "      - projects\n"
        "      - work_types\n"
        "      - commits\n"
        "      - open_threads\n"
        "    sort:\n"
        "      - property: date\n"
        "        direction: DESC\n"
        "  - type: table\n"
        "    name: Projects\n"
        "    filters:\n"
        "      and:\n"
        '        - type == "devlog-project"\n'
        "    order:\n"
        "      - file.name\n"
        "      - active_days\n"
        "      - active_minutes\n"
        "      - open_threads\n"
        "      - commits\n"
        "      - last_active\n"
        "      - repo\n"
        "    sort:\n"
        "      - property: last_active\n"
        "        direction: DESC\n"
        "  - type: table\n"
        "    name: Weeks\n"
        "    filters:\n"
        "      and:\n"
        '        - type == "devlog-week"\n'
        "    order:\n"
        "      - file.name\n"
        "      - active_days\n"
        "      - active_minutes\n"
        "      - commits\n"
        "      - projects\n"
        "    sort:\n"
        "      - property: start\n"
        "        direction: DESC\n"
    )


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


def harvest_thread_state(paths: list[Path], done: set[str]) -> set[str]:
    """Apply checkbox ticks made in Obsidian to the done set.

    Only checkboxes inside managed blocks count (they're all ours). A tick
    anywhere marks a thread done; unticking it where it was shown ticked
    reopens it.
    """
    checked: set[str] = set()
    unchecked: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        start, end = text.find(START), text.find(END)
        if start == -1 or end == -1:
            continue
        for line in text[start:end].split("\n"):
            match = _CHECKBOX_RE.match(line)
            if match:
                key = thread_key(match.group("text"))
                (unchecked if match.group("mark") == " " else checked).add(key)
    return (done - unchecked) | checked


def refresh_graph(vault: Path, folder: str, days: dict[str, dict],
                  done: set[str] | None = None) -> dict:
    """Regenerate every managed note from `days`. Writes only changed files."""
    g = Graph(vault, folder)
    names = _project_names(days)
    display = {slug: c.most_common(1)[0][0] for slug, c in names.items()}
    written: list[Path] = []

    thread_notes = [g.path(g.day_rel(d)) for d in days]
    thread_notes += [g.path(g.project_rel(s)) for s in names]
    done = harvest_thread_state(thread_notes, set(done or ()))

    def emit(rel: str, text: str) -> None:
        path = g.path(rel)
        if _write_if_changed(path, text):
            written.append(path)

    neighbors = _neighbors(days)
    for d, meta in days.items():
        prev_d, next_d = neighbors[d]
        emit(g.day_rel(d), render_day(g, meta, prev_d, next_d, display, done))

    active_days = {d: m for d, m in days.items() if not is_empty_day(m)}
    for slug, counter in names.items():
        emit(g.project_rel(slug), render_project(g, slug, active_days, counter, done))
    work_types = {w for m in active_days.values() for w in m.get("work_types") or []}
    for slug in work_types:
        emit(g.work_rel(slug), render_work(g, slug, active_days, display))
    weeks = sorted({iso_week(d) for d in days})
    for week in weeks:
        emit(g.period_rel("week", week), render_period(g, "week", week, weeks, days, display))
    months = sorted({month_of(d) for d in days})
    for month in months:
        emit(g.period_rel("month", month),
             render_period(g, "month", month, months, days, display))
    if days:
        emit(g.home_rel(), render_home(g, days, display, done))
        base = g.root / f"{BASE_NAME}.base"
        if not base.exists():  # written once; the user may customize views
            base.parent.mkdir(parents=True, exist_ok=True)
            base.write_text(render_base(g.folder), encoding="utf-8")
            written.append(base)

    removed = _prune_stale(g.root / PROJECTS_DIR, set(names))
    removed += _prune_stale(g.root / WORK_DIR, work_types)
    removed += _prune_stale(g.root / WEEKLY_DIR, set(weeks))
    removed += _prune_stale(g.root / MONTHLY_DIR, set(months))
    return {
        "written": [str(p) for p in written],
        "removed": [str(p) for p in removed],
        "done_threads": done,
    }


def existing_day_notes(folder_root: Path) -> set[str]:
    if not folder_root.is_dir():
        return set()
    return {p.stem for p in folder_root.iterdir() if _DAY_FILE_RE.match(p.name)}
