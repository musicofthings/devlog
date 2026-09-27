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

import copy
import hashlib
import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from devlog.knowledge import headline, is_empty_day
from devlog.pricing import estimate, format_cost, merge_tokens, price_table
from devlog.related import Corpus, cosine_neighbors, day_document
from devlog.topics import TopicDetector
from devlog.worktypes import describe

INDEX_VERSION = 3
START = "%% devlog:start %%"
END = "%% devlog:end %%"
DEFAULT_TAIL = "\n\n## Notes\n\n"
TOPIC_TAIL = (
    "\n\n## Literature & notes\n\n"
    "%% Yours: Zotero citekeys (`[[@citekey]]`), paper links, protocols. Never overwritten. %%\n\n"
)
HOME_NAME = "DevLog Home"
BASE_NAME = "DevLog"
PROJECTS_DIR = "Projects"
WORK_DIR = "Work"
WEEKLY_DIR = "Weekly"
MONTHLY_DIR = "Monthly"
QUARTERLY_DIR = "Quarterly"
TOPICS_DIR = "Topics"
CANVAS_DIR = "Canvas"
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


def load_state(folder_root: Path) -> dict:
    """Persisted refresh state: ticked threads and hashes of generated canvases."""
    data = _read_index(folder_root)
    done = data.get("done_threads")
    hashes = data.get("canvas_hashes")
    if not isinstance(done, list):
        done = []
    if not isinstance(hashes, dict):
        hashes = {}
    return {
        "done_threads": {d for d in done if isinstance(d, str)},
        "canvas_hashes": {k: v for k, v in hashes.items() if isinstance(v, str)},
    }


def load_done_threads(folder_root: Path) -> set[str]:
    return load_state(folder_root)["done_threads"]


def save_index(folder_root: Path, days: dict[str, dict], state: dict | None = None) -> None:
    """Write days + state. `state=None` keeps the state already on disk."""
    state = {**load_state(folder_root), **(state or {})}
    path = index_path(folder_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": INDEX_VERSION,
        "days": dict(sorted(days.items())),
        "done_threads": sorted(state["done_threads"]),
        "canvas_hashes": dict(sorted(state["canvas_hashes"].items())),
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


def _tail(path: Path, legacy_generated: str | None = None,
          default: str = DEFAULT_TAIL) -> str:
    """User-owned text after the managed block (default Notes heading if none).

    A pre-graph day note has no END marker. If its body differs from what the
    old mirror generated (`legacy_generated`), the user edited it: keep the
    whole old body under Notes rather than silently dropping it.
    """
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    idx = text.find(END)
    if idx != -1:
        return text[idx + len(END) :] or default
    body = _FRONTMATTER_RE.sub("", text, count=1).strip()
    if not body or legacy_generated is None or body == legacy_generated.strip():
        return DEFAULT_TAIL
    return f"{DEFAULT_TAIL}### Kept from the previous version of this note\n\n{body}\n"


def _is_untouched(path: Path) -> bool:
    tail = _tail(path).strip()
    return tail in {"", "## Notes"} or tail == TOPIC_TAIL.strip()


def _compose(
    frontmatter: list[str], body: list[str], path: Path, legacy_generated: str | None = None,
    default_tail: str = DEFAULT_TAIL,
) -> str:
    fm = "---\n" + "\n".join(frontmatter) + "\n---\n"
    managed = START + "\n" + "\n".join(body).strip("\n") + "\n" + END
    return fm + managed + _tail(path, legacy_generated, default_tail)


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


def _cost(g: Graph, *token_maps) -> tuple[float, str]:
    """(USD, display text) for any number of tokens_by_model maps."""
    usd, unpriced = estimate(merge_tokens(*token_maps), g.prices)
    return usd, format_cost(usd, unpriced) if (usd or unpriced) else ""


def _add_tokens(total: dict[str, int], tokens: dict | None) -> None:
    for key in ("in", "out", "cache"):
        total[key] = total.get(key, 0) + int((tokens or {}).get(key) or 0)


def thread_key(text: str) -> str:
    """Identity of an open thread across notes: rendered text, case/space-folded."""
    text = _THREAD_SUFFIX_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip().lower()


class Graph:
    """Path and link conventions for one vault folder."""

    def __init__(self, vault: Path, folder: str, prices: dict | None = None) -> None:
        self.prices = prices if prices is not None else price_table()
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
        folder = {"week": WEEKLY_DIR, "month": MONTHLY_DIR, "quarter": QUARTERLY_DIR}[kind]
        return self._rel(folder, label)

    def period(self, kind: str, label: str, *, table: bool = False) -> str:
        return self.link(self.period_rel(kind, label), label, table=table)

    def home_rel(self) -> str:
        return self._rel(HOME_NAME)

    def topic_rel(self, slug: str) -> str:
        return self._rel(TOPICS_DIR, slug)

    def canvas_file(self, slug: str) -> str:
        return self._rel(CANVAS_DIR, f"{slug}.canvas")

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

    def topic(self, slug: str, name: str, *, table: bool = False) -> str:
        return self.link(self.topic_rel(slug), name, table=table)


def iso_week(day: str) -> str:
    year, week, _ = date.fromisoformat(day).isocalendar()
    return f"{year}-W{week:02d}"


def month_of(day: str) -> str:
    return day[:7]


def quarter_of(day: str) -> str:
    return f"{day[:4]}-Q{(int(day[5:7]) - 1) // 3 + 1}"


PERIOD_KEY = {"week": iso_week, "month": month_of, "quarter": quarter_of}


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
               display: dict[str, str], done: set[str],
               topic_names: dict[str, str] | None = None) -> str:
    topic_names = topic_names or {}
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
    day_usd = _cost(g, *(p.get("tokens_by_model") for p in projects))[0]
    if day_usd:
        fm.append(f"cost_usd: {day_usd:.2f}")
    open_threads = [t for p in projects for t in p.get("threads") or []
                    if thread_key(safe_text(t)) not in done]
    if open_threads:
        fm.append(f"open_threads: {len(open_threads)}")
    fm += _yaml_list("projects", [g.project(p["slug"], display.get(p["slug"])) for p in projects])
    fm += _yaml_list("work_types", [g.work(w) for w in work])
    topics = meta.get("topics") or []
    if topics:
        fm += _yaml_list("topics", [g.topic(t, topic_names.get(t, t)) for t in topics])
    if meta.get("sources"):
        fm += _yaml_list("sources", meta["sources"], quote=False)
    tags = ["devlog"] + [f"devlog/project/{_tag(p['slug'])}" for p in projects]
    tags += [f"devlog/work/{_tag(w)}" for w in work]
    tags += [f"devlog/topic/{_tag(t)}" for t in topics]
    fm += _yaml_list("tags", tags, quote=False)

    nav = [f"← {g.day(prev_day)}" if prev_day else "← (first)", g.week(week), g.month(month),
           g.home(), f"{g.day(next_day)} →" if next_day else "(latest) →"]
    body = [" · ".join(nav), "", f"# {day}", "", (meta.get("summary") or "").strip(), ""]
    if topics:
        body += ["**Topics:** " + " · ".join(g.topic(t, topic_names.get(t, t)) for t in topics),
                 ""]

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
                cost = _cost(g, p.get("tokens_by_model"))[1]
                body.append(f"- **Tokens:** {token_line}" + (f" · {cost}" if cost else ""))
            if p.get("threads"):
                body.append("- **Open threads** (from the session recap):")
                body += ["    " + _checkbox(t, done) for t in p["threads"]]
    if meta.get("related"):
        body += ["", "## Related days", ""]
        for rel in meta["related"]:
            line = f"- {g.day(rel['date'])}"
            if rel.get("projects"):
                line += " · " + ", ".join(g.project(sl, display.get(sl)) for sl in rel["projects"])
            if rel.get("terms"):
                line += " — shared: " + ", ".join(safe_text(t) for t in rel["terms"])
            body.append(line)
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
                   done: set[str], topic_names: dict[str, str] | None = None,
                   canvas: bool = False, info: dict | None = None) -> str:
    topic_names = topic_names or {}
    info = info or {}
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
    topics = Counter(t for _, _, p in entries for t in p.get("topics") or [])
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
    project_usd = _cost(g, *(p.get("tokens_by_model") for _, _, p in entries))[0]
    if project_usd:
        fm.append(f"cost_usd: {project_usd:.2f}")
    fm += _yaml_list("work_types", [g.work(w) for w, _ in work.most_common()])
    if topics:
        fm += _yaml_list("topics", [g.topic(t, topic_names.get(t, t))
                                    for t, _ in topics.most_common()])
    fm += _yaml_list("tags", ["devlog", "devlog/project-hub",
                              f"devlog/project/{_tag(slug)}"], quote=False)

    summary = (f"> **{len(entries)}** active day(s) · **{_sum_minutes(minutes)}** active min · "
               f"first {g.day(first)} · last {g.day(last)}")
    if commits:
        summary += f" · **{commits}** commit(s)"
    body = [f"# {display}", "", "> [!summary] Activity", summary]
    token_line = _token_line(tokens)
    hub_usd, hub_cost = _cost(g, *(p.get("tokens_by_model") for _, _, p in entries))
    if token_line:
        body.append(f"> Tokens: {token_line}" + (f" · {hub_cost}" if hub_cost else ""))
    body.append("")
    if repo:
        body.append(f"**Repo:** [{repo.removeprefix('https://')}]({repo})")
    if work:
        body.append("**Work mix:** " + " · ".join(f"{g.work(w)} ×{n}"
                                                   for w, n in work.most_common()))
    if topics:
        body.append("**Topics:** " + " · ".join(f"{g.topic(t, topic_names.get(t, t))} ×{n}"
                                                for t, n in topics.most_common(12)))
    if sources:
        body.append("**Sources:** " + ", ".join(sources))
    if canvas:
        body.append(f"**Canvas:** [[{g.canvas_file(slug)}|timeline canvas]]")
    if files:
        body.append("**Frequently touched files:** " + ", ".join(
            f"`{f.replace('`', '')}` ({n})" for f, n in files.most_common(10)))
    if info.get("readme"):
        body += ["", "## About", "", "> " + safe_text(info["readme"])]
    if info.get("pull_requests"):
        body += ["", "## Open pull requests", ""]
        for pr in info["pull_requests"]:
            draft = " (draft)" if pr.get("draft") else ""
            body.append(f"- [#{pr['number']}]({pr['url']}) {safe_text(pr['title'])}{draft}")
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
    if kind == "quarter":
        year, q = label.split("-Q")
        start = date(int(year), 3 * (int(q) - 1) + 1, 1)
        months = 3
    else:
        start = date.fromisoformat(f"{label}-01")
        months = 1
    month_index = start.month - 1 + months
    nxt = date(start.year + month_index // 12, month_index % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


def _period_review(g: Graph, kind: str, start: date, days: dict[str, dict],
                   in_period: list[str], display: dict[str, str],
                   topic_names: dict[str, str]) -> list[str]:
    """Deterministic retro: this period vs the previous one, plus what was new."""
    key = PERIOD_KEY[kind]
    prev_label = key((start - timedelta(days=1)).isoformat())

    def link(label: str) -> str:
        return g.period(kind, label)
    prev_days = [d for d in days if key(d) == prev_label]

    def mins(ds: list[str]) -> int:
        return sum(int(days[d].get("active_minutes") or 0) for d in ds)

    def active(ds: list[str]) -> int:
        return sum(1 for d in ds if not is_empty_day(days[d]))

    cur_m, prev_m = mins(in_period), mins(prev_days)
    lines = ["## Review", ""]
    if prev_days:
        delta = "" if not prev_m else f" ({(cur_m - prev_m) / prev_m:+.0%})"
        lines.append(f"- **Active time:** {cur_m:,} min vs {prev_m:,} in {link(prev_label)}{delta}")
        lines.append(f"- **Active days:** {active(in_period)} vs {active(prev_days)}")
    earlier = {p["slug"] for d, m in days.items() if d < start.isoformat()
               for p in m.get("projects") or []}
    here = {p["slug"] for d in in_period for p in days[d].get("projects") or []}
    new_projects = sorted(here - earlier)
    if new_projects and earlier:
        lines.append("- **New projects:** " + ", ".join(
            g.project(sl, display.get(sl)) for sl in new_projects))
    earlier_topics = {t for d, m in days.items() if d < start.isoformat()
                      for t in m.get("topics") or []}
    topic_counts = Counter(t for d in in_period for t in days[d].get("topics") or [])
    new_topics = sorted(set(topic_counts) - earlier_topics)
    if topic_counts:
        lines.append("- **Top topics:** " + ", ".join(
            f"{g.topic(t, topic_names.get(t, t))} ×{n}" for t, n in topic_counts.most_common(5)))
    if new_topics and earlier_topics:
        lines.append("- **First time:** " + ", ".join(
            g.topic(t, topic_names.get(t, t)) for t in new_topics))
    raised = sum(len(p.get("threads") or []) for d in in_period
                 for p in days[d].get("projects") or [])
    if raised:
        lines.append(f"- **Threads raised:** {raised}")
    return lines + [""] if len(lines) > 2 else []


def _plain(line: str) -> str:
    """Markdown line -> plain text for model prompts ([[a|b]] -> b, no bold)."""
    line = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", line)
    line = re.sub(r"\[\[([^\]]*)\]\]", r"\1", line)
    return line.replace("**", "").replace("\\|", "|")


def render_period(g: Graph, kind: str, label: str, labels: list[str], days: dict[str, dict],
                  display: dict[str, str], topic_names: dict[str, str] | None = None,
                  retro=None) -> str:
    """Weekly, monthly, or quarterly rollup."""
    topic_names = topic_names or {}
    start, end = _period_bounds(kind, label)
    key = PERIOD_KEY[kind]

    def link(other: str) -> str:
        return g.period(kind, other)
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
    span = f"{start:%b %d} – {end:%b %d, %Y}"
    if kind == "week":
        months = sorted({month_of(start.isoformat()), month_of(end.isoformat())})
        nav.insert(1, " / ".join(g.month(m) for m in months))
        title = label
    elif kind == "month":
        nav.insert(1, g.period("quarter", quarter_of(start.isoformat())))
        title = f"{start:%B %Y}"
    else:
        title = label.replace("-", " ")
    stats = f"{span} · **{len(active)}** active day(s) · **{minutes:,}** active min"
    token_line = _token_line(tokens)
    period_usd, period_cost = _cost(
        g, *(p.get("tokens_by_model") for d in active for p in days[d].get("projects") or []))
    if period_usd:
        fm.append(f"cost_usd: {period_usd:.2f}")
    body = [" · ".join(nav), "", f"# {title}", "", stats]
    if token_line:
        body += ["", f"Tokens: {token_line}" + (f" · {period_cost}" if period_cost else "")]
    body.append("")
    if active:
        review = _period_review(g, kind, start, days, in_period, display, topic_names)
        if retro is not None:
            facts = [stats, *review[2:]] + [
                f"{d}: {headline(days[d])}" for d in active if headline(days[d])]
            text = retro(label, "\n".join(_plain(f) for f in facts if f))
            if text:
                review = [*review, "> [!quote] Retro (written by a local model)",
                          *[f"> {safe_text(line)}" for line in text.splitlines() if line.strip()],
                          ""]
        body += review
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
    if kind == "quarter":
        months = sorted({month_of(d) for d in in_period})
        body += ["**Months:** " + " · ".join(g.month(m) for m in months), ""]
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
                done: set[str], topic_names: dict[str, str] | None = None) -> str:
    topic_names = topic_names or {}
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

    current, longest = streaks(days)
    total_usd, total_cost = _cost(
        g, *(p.get("tokens_by_model") for d in active for p in days[d].get("projects") or []))
    topics = Counter(t for d in active for t in days[d].get("topics") or [])
    fm = ["type: devlog-home", f"active_days: {len(active)}", f"active_minutes: {total}",
          f"open_threads: {sum(open_by_project.values())}",
          f"current_streak: {current}", f"longest_streak: {longest}"]
    if total_usd:
        fm.append(f"cost_usd: {total_usd:.2f}")
    fm += _yaml_list("tags", ["devlog", "devlog/home"], quote=False)
    body = [f"# {HOME_NAME}", "",
            f"**{len(active)}** active day(s) · **{total:,}** active min · "
            f"**{len(proj_days)}** project(s) · **{sum(open_by_project.values())}** open thread(s)"
            + (f" · {g.day(active[-1])} → {g.day(active[0])}" if active else ""),
            "",
            f"🔥 Streak: **{current}** day(s) · longest **{longest}**"
            + (f" · API-equivalent cost {total_cost}" if total_cost else ""),
            ""]
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
    if topics:
        body += ["## Topics", "",
                 " · ".join(f"{g.topic(t, topic_names.get(t, t))} ×{n}"
                            for t, n in topics.most_common(20)), ""]
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
    quarters = sorted({quarter_of(d) for d in days}, reverse=True)
    if quarters:
        body += ["## Quarters", "", " · ".join(g.period("quarter", q) for q in quarters), ""]
    if months:
        body += ["## Months", "", " · ".join(g.month(m) for m in months), ""]
    if weeks:
        body += ["## Weeks", "", " · ".join(g.week(w) for w in weeks), ""]
    folder = g.folder or "/"
    pages_source = f"'\"{g.folder}\"'" if g.folder else ""
    base = "/".join(p for p in (g.folder, f"{BASE_NAME}.base") if p)
    body += [
        "## Views",
        "",
        "> [!example]- Obsidian Bases (core plugin, Obsidian 1.9+)",
        f"> ![[{base}]]",
        "",
        "> [!tip]- Activity heatmap (needs Dataview + Heatmap Calendar community plugins)",
        "> ```dataviewjs",
        f"> const entries = dv.pages({pages_source})",
        ">   .where(p => p.type == \"devlog-day\" && p.active_minutes > 0)",
        ">   .map(p => ({date: p.file.name, intensity: p.active_minutes,",
        ">               content: p.active_minutes + \" min\"})).array();",
        "> renderHeatmapCalendar(this.container, {year: new Date().getFullYear(), entries});",
        "> ```",
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


# ---------------------------------------------------------------- phase 3


def annotate(days: dict[str, dict], detector: TopicDetector,
             embedder=None) -> dict[str, dict]:
    """Copy of `days` with derived, never-persisted keys.

    Topics (per project and per day) and related days are recomputed on every
    refresh, so a new custom topic in config.toml applies to the whole history
    on the next `devlog obsidian --reindex`.
    """
    out = copy.deepcopy(days)
    for meta in out.values():
        day_topics: set[str] = set()
        for p in meta.get("projects") or []:
            texts = [*(p.get("tasks") or []), *(p.get("threads") or []),
                     *(c.get("subject") or "" for c in p.get("commits") or []),
                     *(p.get("files") or [])]
            p["topics"] = detector.detect(texts)
            day_topics.update(p["topics"])
        if not meta.get("projects"):
            day_topics.update(detector.detect([meta.get("summary") or ""]))
        meta["topics"] = sorted(day_topics)

    active = {d: m for d, m in out.items() if not is_empty_day(m)}
    docs = {d: day_document(m, [detector.names.get(t, t) for t in m["topics"]])
            for d, m in active.items()}
    corpus = Corpus(docs)
    vectors = None
    if embedder is not None and docs:
        try:
            vectors = embedder(docs)  # optional local embeddings (devlog.local_llm)
        except Exception:  # noqa: BLE001 - any backend failure falls back to TF-IDF
            vectors = None
    for d in active:
        if vectors is not None:
            pairs = [(o, sc, corpus.shared_terms(d, o))
                     for o, sc in cosine_neighbors(vectors, d, k=3)]
        else:
            pairs = corpus.similar(d, k=3)
        out[d]["related"] = [
            {"date": other, "score": round(score, 3), "terms": terms,
             "projects": [p["slug"] for p in out[other].get("projects") or []]}
            for other, score, terms in pairs
        ]
    return out


def streaks(days: dict[str, dict]) -> tuple[int, int]:
    """(current, longest) runs of consecutive active days.

    The current streak ends at the latest logged day; a quiet latest day
    means the current streak is 0.
    """
    active = sorted(date.fromisoformat(d) for d, m in days.items() if not is_empty_day(m))
    if not active:
        return 0, 0
    longest = run = 1
    for prev, cur in zip(active, active[1:], strict=False):
        run = run + 1 if cur - prev == timedelta(days=1) else 1
        longest = max(longest, run)
    latest = date.fromisoformat(max(days))
    current = 0
    active_set = set(active)
    probe = latest
    while probe in active_set:
        current += 1
        probe -= timedelta(days=1)
    return current, longest


def render_topic(g: Graph, slug: str, name: str, category: str, days: dict[str, dict],
                 display: dict[str, str], topic_names: dict[str, str]) -> str:
    matching = sorted((d for d, m in days.items() if slug in (m.get("topics") or [])),
                      reverse=True)
    projects: Counter = Counter()
    co_topics: Counter = Counter()
    for d in matching:
        for p in days[d].get("projects") or []:
            if slug in (p.get("topics") or []):
                projects[p["slug"]] += 1
        co_topics.update(t for t in days[d].get("topics") or [] if t != slug)
    first, last = matching[-1], matching[0]
    fm = ["type: devlog-topic", f"topic: {_q(name)}", f"category: {_q(category)}",
          f"active_days: {len(matching)}", f"first_active: {first}", f"last_active: {last}"]
    fm += _yaml_list("projects", [g.project(p, display.get(p)) for p, _ in projects.most_common()])
    fm += _yaml_list("tags", ["devlog", "devlog/topic-hub", f"devlog/topic/{_tag(slug)}"],
                     quote=False)
    body = [f"# {name}", "", f"> [!info] {category}",
            f"> **{len(matching)}** day(s) · first {g.day(first)} · last {g.day(last)}", ""]
    if projects:
        body.append("**Projects:** " + " · ".join(
            f"{g.project(p, display.get(p))} ×{n}" for p, n in projects.most_common()))
    if co_topics:
        body.append("**Often together with:** " + " · ".join(
            f"{g.topic(t, topic_names.get(t, t))} ×{n}" for t, n in co_topics.most_common(8)))
    body += ["", "## Days", "", "| Day | Projects | Focus |", "|---|---|---|"]
    for d in matching:
        meta = days[d]
        hits = [p for p in meta.get("projects") or [] if slug in (p.get("topics") or [])]
        names = ", ".join(g.project(p["slug"], display.get(p["slug"]), table=True)
                          for p in (hits or meta.get("projects") or []))
        focus = headline({"projects": hits} if hits else meta)
        body.append(f"| {g.day(d, table=True)} | {names} | {safe_text(focus, table=True)} |")
    return _compose(fm, body, g.path(g.topic_rel(slug)), default_tail=TOPIC_TAIL)


CANVAS_DAYS = 12
_W, _H, _GAP = 400, 480, 60


def render_canvas(g: Graph, slug: str, days: dict[str, dict],
                  topic_names: dict[str, str]) -> str:
    """JSON Canvas: project hub → its latest active days in order, topics below."""
    entries = sorted(d for d, m in days.items()
                     if any(p["slug"] == slug for p in m.get("projects") or []))
    shown = entries[-CANVAS_DAYS:]
    topics: Counter = Counter(
        t for d in entries for p in days[d].get("projects") or []
        if p["slug"] == slug for t in p.get("topics") or []
    )
    nodes: list[dict] = [{"id": "hub", "type": "file", "file": g.project_rel(slug) + ".md",
                          "x": 0, "y": 0, "width": _W, "height": _H, "color": "6"}]
    edges: list[dict] = []
    x0 = _W + 2 * _GAP
    nodes.append({"id": "timeline", "type": "group", "label": f"Last {len(shown)} active day(s)",
                  "x": x0 - _GAP // 2, "y": -_GAP,
                  "width": len(shown) * (_W + _GAP), "height": _H + 2 * _GAP})
    prev = "hub"
    for i, d in enumerate(shown):
        node_id = f"day-{d}"
        nodes.append({"id": node_id, "type": "file", "file": g.day_rel(d) + ".md",
                      "x": x0 + i * (_W + _GAP), "y": 0, "width": _W, "height": _H})
        edges.append({"id": f"e-{prev}-{node_id}", "fromNode": prev, "fromSide": "right",
                      "toNode": node_id, "toSide": "left"})
        prev = node_id
    for j, (t, _n) in enumerate(topics.most_common(6)):
        node_id = f"topic-{t}"
        nodes.append({"id": node_id, "type": "file", "file": g.topic_rel(t) + ".md",
                      "x": j * (_W + _GAP), "y": _H + 3 * _GAP, "width": _W, "height": 260,
                      "color": "5"})
        edges.append({"id": f"e-hub-{node_id}", "fromNode": "hub", "fromSide": "bottom",
                      "toNode": node_id, "toSide": "top"})
    return json.dumps({"nodes": nodes, "edges": edges}, indent=2) + "\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def _latest(days: dict[str, dict], slug: str, key: str) -> str | None:
    for d in sorted(days, reverse=True):
        for p in days[d].get("projects") or []:
            if p["slug"] == slug and p.get(key):
                return p[key]
    return None


def refresh_graph(vault: Path, folder: str, days: dict[str, dict],
                  done: set[str] | None = None, *,
                  detector: TopicDetector | None = None,
                  canvas_hashes: dict[str, str] | None = None,
                  project_info=None, prices: dict | None = None,
                  embedder=None, retro=None) -> dict:
    """Regenerate every managed note from `days`. Writes only changed files.

    Returns written/removed paths plus the updated persisted state
    (`done_threads`, `canvas_hashes`) for the caller to save.
    """
    g = Graph(vault, folder, prices)
    detector = detector or TopicDetector()
    topic_names = detector.names
    raw_days = days
    days = annotate(raw_days, detector, embedder)
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
        emit(g.day_rel(d), render_day(g, meta, prev_d, next_d, display, done, topic_names))

    active_days = {d: m for d, m in days.items() if not is_empty_day(m)}

    # Canvases first, so hubs only link canvases that exist.
    hashes = dict(canvas_hashes or {})
    canvases: set[str] = set()
    for slug in names:
        rel = g.canvas_file(slug)
        path = vault / rel
        text = render_canvas(g, slug, active_days, topic_names)
        if path.exists():
            current = _sha(path.read_text(encoding="utf-8"))
            if hashes.get(rel) != current:
                canvases.add(slug)  # rearranged by the user: theirs now
                continue
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            written.append(path)
        hashes[rel] = _sha(text)
        canvases.add(slug)

    for slug, counter in names.items():
        # project_info(root, repo_url) -> README blurb / open PRs; None keeps it offline.
        info = project_info(_latest(days, slug, "root"), _latest(days, slug, "repo_url")) \
            if project_info else None
        emit(g.project_rel(slug), render_project(g, slug, active_days, counter, done,
                                                 topic_names, canvas=slug in canvases,
                                                 info=info))
    work_types = {w for m in active_days.values() for w in m.get("work_types") or []}
    for slug in work_types:
        emit(g.work_rel(slug), render_work(g, slug, active_days, display))
    topics = {t for m in active_days.values() for t in m.get("topics") or []}
    for slug in topics:
        emit(g.topic_rel(slug), render_topic(g, slug, topic_names.get(slug, slug),
                                             detector.categories.get(slug, "custom"),
                                             active_days, display, topic_names))
    weeks = sorted({iso_week(d) for d in days})
    for week in weeks:
        emit(g.period_rel("week", week),
             render_period(g, "week", week, weeks, days, display, topic_names, retro))
    months = sorted({month_of(d) for d in days})
    for month in months:
        emit(g.period_rel("month", month),
             render_period(g, "month", month, months, days, display, topic_names, retro))
    quarters = sorted({quarter_of(d) for d in days})
    for quarter in quarters:
        emit(g.period_rel("quarter", quarter),
             render_period(g, "quarter", quarter, quarters, days, display, topic_names))
    if days:
        emit(g.home_rel(), render_home(g, days, display, done, topic_names))
        base = g.root / f"{BASE_NAME}.base"
        if not base.exists():  # written once; the user may customize views
            base.parent.mkdir(parents=True, exist_ok=True)
            base.write_text(render_base(g.folder), encoding="utf-8")
            written.append(base)

    removed = _prune_stale(g.root / PROJECTS_DIR, set(names))
    removed += _prune_stale(g.root / WORK_DIR, work_types)
    removed += _prune_stale(g.root / TOPICS_DIR, topics)
    removed += _prune_stale(g.root / WEEKLY_DIR, set(weeks))
    removed += _prune_stale(g.root / MONTHLY_DIR, set(months))
    removed += _prune_stale(g.root / QUARTERLY_DIR, set(quarters))
    canvas_dir = g.root / CANVAS_DIR
    if canvas_dir.is_dir():
        for path in canvas_dir.glob("*.canvas"):
            rel = g.canvas_file(path.stem)
            if path.stem in names or hashes.get(rel) != _sha(path.read_text(encoding="utf-8")):
                continue  # still used, or edited by the user
            path.unlink()
            hashes.pop(rel, None)
            removed.append(path)
    return {
        "written": [str(p) for p in written],
        "removed": [str(p) for p in removed],
        "done_threads": done,
        "canvas_hashes": hashes,
    }


def existing_day_notes(folder_root: Path) -> set[str]:
    if not folder_root.is_dir():
        return set()
    return {p.stem for p in folder_root.iterdir() if _DAY_FILE_RE.match(p.name)}
