"""Query the vault index as agent memory ("what did I do on X, what's open?").

Plain functions returning short markdown, so they're easy to test and to
expose through the MCP server in `devlog/mcp_server.py`. The index is reread
on every call, so a long-running server sees each night's publish.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from devlog.config import DevlogConfig
from devlog.knowledge import headline, is_empty_day, project_slug
from devlog.related import Corpus, day_document
from devlog.topics import TopicDetector
from devlog.vault_graph import (
    annotate,
    load_index,
    load_state,
    project_open_threads,
    safe_text,
    thread_key,
)


class VaultMemory:
    def __init__(self, cfg: DevlogConfig) -> None:
        vault = Path(cfg.obsidian_vault).expanduser() if cfg.obsidian_vault.strip() else None
        folder = (cfg.obsidian_folder or "DevLog").strip().strip("/\\")
        self.root = (vault / folder if folder else vault) if vault else None
        self.detector = TopicDetector(cfg.topics)

    def _load(self) -> tuple[dict[str, dict], set[str]]:
        if self.root is None or not self.root.is_dir():
            return {}, set()
        days = annotate(load_index(self.root), self.detector)
        # One display name per project (the most common spelling), everywhere.
        names: dict[str, Counter] = {}
        for m in days.values():
            for p in m.get("projects") or []:
                names.setdefault(p["slug"], Counter())[p.get("name") or p["slug"]] += 1
        for m in days.values():
            for p in m.get("projects") or []:
                p["name"] = names[p["slug"]].most_common(1)[0][0]
        return days, load_state(self.root)["done_threads"]

    def _resolve_project(self, days: dict[str, dict], project: str) -> str | None:
        want = project_slug(project)
        slugs = {p["slug"]: p.get("name") or p["slug"]
                 for m in days.values() for p in m.get("projects") or []}
        if want in slugs:
            return want
        for slug, name in slugs.items():
            if want in slug or project.lower() in name.lower():
                return slug
        return None

    @staticmethod
    def _day_line(d: str, meta: dict) -> str:
        projects = ", ".join(p.get("name") or p["slug"] for p in meta.get("projects") or [])
        focus = headline(meta)
        line = f"- {d} · {meta.get('active_minutes') or 0} min"
        line += f" · {projects}" if projects else ""
        line += f" — {focus}" if focus else ""
        return line

    # ------------------------------------------------------------ queries

    def list_projects(self) -> str:
        days, done = self._load()
        stats: dict[str, dict] = {}
        for d in sorted(days):
            for p in days[d].get("projects") or []:
                s = stats.setdefault(p["slug"], {"name": p.get("name") or p["slug"],
                                                 "days": 0, "minutes": 0, "last": d})
                s["days"] += 1
                s["minutes"] += p.get("minutes") or 0
                s["last"] = d
        if not stats:
            return "No projects in the devlog vault yet."
        rows = ["| Project | Days | Min | Open threads | Last active |", "|---|---|---|---|---|"]
        for slug, s in sorted(stats.items(), key=lambda kv: kv[1]["last"], reverse=True):
            open_n = len(project_open_threads(slug, days, done))
            rows.append(f"| {s['name']} | {s['days']} | {s['minutes']} | {open_n} | {s['last']} |")
        return "\n".join(rows)

    def recent_activity(self, days_back: int = 7, project: str | None = None,
                        today: date | None = None) -> str:
        days, _ = self._load()
        if not days:
            return "The devlog vault is empty."
        end = today or date.fromisoformat(max(days))
        start = end - timedelta(days=max(days_back, 1) - 1)
        slug = self._resolve_project(days, project) if project else None
        if project and slug is None:
            return f"No project matching {project!r}."
        lines = []
        for d in sorted(days, reverse=True):
            if not (start.isoformat() <= d <= end.isoformat()) or is_empty_day(days[d]):
                continue
            meta = days[d]
            if slug:
                hits = [p for p in meta.get("projects") or [] if p["slug"] == slug]
                if not hits:
                    continue
                meta = {**meta, "projects": hits, "active_minutes": hits[0].get("minutes")}
            lines.append(self._day_line(d, meta))
        scope = f" on {project}" if project else ""
        if not lines:
            return f"No logged activity{scope} between {start} and {end}."
        return f"Activity{scope}, {start} → {end}:\n" + "\n".join(lines)

    def project_status(self, project: str) -> str:
        days, done = self._load()
        slug = self._resolve_project(days, project)
        if slug is None:
            return f"No project matching {project!r}."
        entries = [(d, p) for d in sorted(days, reverse=True)
                   for p in days[d].get("projects") or [] if p["slug"] == slug]
        name = entries[0][1].get("name") or slug
        minutes = sum(p.get("minutes") or 0 for _, p in entries)
        work = Counter(w for _, p in entries for w in p.get("work_types") or [])
        topics = Counter(t for _, p in entries for t in p.get("topics") or [])
        repo = next((p["repo_url"] for _, p in entries if p.get("repo_url")), None)
        out = [f"# {name}",
               f"{len(entries)} active day(s), {minutes} min, first {entries[-1][0]}, "
               f"last {entries[0][0]}." + (f" Repo: {repo}" if repo else "")]
        if work:
            out.append("Work mix: " + ", ".join(f"{w} ×{n}" for w, n in work.most_common(6)))
        if topics:
            out.append("Topics: " + ", ".join(
                f"{self.detector.names.get(t, t)} ×{n}" for t, n in topics.most_common(8)))
        threads = project_open_threads(slug, days, done)
        if threads:
            out += ["", "## Open threads"] + [f"- [ ] {t} (from {d})" for t, d in threads[:15]]
        commits = [(d, c) for d, p in entries for c in p.get("commits") or []][:10]
        if commits:
            out += ["", "## Recent commits"] + [f"- {d} {c['short']} {c['subject']}"
                                                for d, c in commits]
        out += ["", "## Recent days"]
        for d, p in entries[:7]:
            out.append(self._day_line(d, {**days[d], "projects": [p],
                                          "active_minutes": p.get("minutes")}))
        return "\n".join(out)

    def open_threads(self, project: str | None = None) -> str:
        days, done = self._load()
        if project:
            slug = self._resolve_project(days, project)
            if slug is None:
                return f"No project matching {project!r}."
            slugs = [slug]
        else:
            slugs = sorted({p["slug"] for m in days.values() for p in m.get("projects") or []})
        out = []
        for slug in slugs:
            threads = project_open_threads(slug, days, done)
            if threads:
                out.append(f"## {slug}")
                out += [f"- [ ] {t} (from {d})" for t, d in threads[:20]]
        return "\n".join(out) or "No open threads."

    def search(self, query: str, limit: int = 8) -> str:
        days, _ = self._load()
        active = {d: m for d, m in days.items() if not is_empty_day(m)}
        corpus = Corpus({
            d: day_document(m, [self.detector.names.get(t, t) for t in m.get("topics") or []])
            for d, m in active.items()
        })
        hits = corpus.search(query, k=max(1, min(limit, 25)))
        if not hits:
            return f"No days match {query!r}."
        lines = []
        for d, _score, terms in hits:
            meta = active[d]
            projects = meta.get("projects") or []
            if len(projects) > 1:
                # Lead with the project whose text actually matched.
                def hits_in(p: dict, terms: list[str] = terms) -> int:
                    blob = day_document({"projects": [p]}).lower()
                    return sum(t in blob for t in terms)

                best = max(projects, key=hits_in)
                meta = {**meta, "projects": [best, *[p for p in projects if p is not best]]}
            lines.append(self._day_line(d, meta) + f"  [matched: {', '.join(terms)}]")
        return "\n".join(lines)

    def day_log(self, day: str) -> str:
        days, done = self._load()
        meta = days.get(day)
        if meta is None:
            return f"No devlog entry for {day}."
        out = [f"# {day}", (meta.get("summary") or "").strip()]
        for p in meta.get("projects") or []:
            out += ["", f"## {p.get('name') or p['slug']} · {p.get('minutes') or '?'} min"]
            if p.get("work_types"):
                out.append("Work: " + ", ".join(p["work_types"]))
            out += [f"- asked: {t}" for t in p.get("tasks") or []]
            out += [f"- commit {c['short']}: {c['subject']}" for c in p.get("commits") or []]
            for t in p.get("threads") or []:
                mark = "x" if thread_key(safe_text(t)) in done else " "
                out.append(f"- [{mark}] {t}")
        if meta.get("related"):
            out.append("\nRelated days: " + ", ".join(r["date"] for r in meta["related"]))
        return "\n".join(out)
