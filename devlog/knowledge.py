"""Structured per-day metadata for the Obsidian knowledge graph.

Public posts are a few sentences of prose; the vault needs the structure
behind them (which projects, how long, what kind of work) to link days to
project and work-type hub notes. Metadata is built from session digests at
publish time, or recovered from the post text when backfilling old posts.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date

from devlog.digest import basename, total_active_minutes
from devlog.literature import reference_dicts
from devlog.models import SessionDigest
from devlog.noise import headline_task, is_injected_prompt, is_low_signal_prompt
from devlog.pipelines import run_thread
from devlog.pricing import merge_tokens
from devlog.privacy import redact_sensitive_text
from devlog.projects import Project, ProjectResolver
from devlog.worktypes import WORK_TYPE_DESCRIPTIONS, classify

MAX_TASKS = 5
MAX_FILES = 8
MAX_TOOLS = 5
MAX_THREADS = 8
MAX_NOTEBOOKS = 8
TASK_CHARS = 160

_SLUG_BAD_RE = re.compile(r"[^a-z0-9._-]+")
# The template post in all three public_detail shapes:
#   verbatim: "... across a, b. I worked on a: <prompt>; b: <prompt>. Tools: X (3x)."
#   projects: "... across a, b. Work: code-review on a; feature on b."
#   summary:  "... in 2 project(s). Work: code-review, feature."
# optionally followed by "Shipped N commit(s)." and "Stack: Python, scanpy."
_TEMPLATE_RE = re.compile(
    r"Today I logged (?P<minutes>\d+) active min "
    r"(?:in (?P<count>\d+) project\(s\)|across (?P<projects>.+?))\.\s*"
    r"(?:I worked on (?P<tasks>.+?)\.\s*)?"
    r"(?:Work: (?P<work>.+?)\.\s*)?"
    r"(?:I recorded activity in (?P<sessions>\d+) coding session\(s\)\.\s*)?"
    r"(?:Shipped (?P<commits>\d+) commit\(s\)\.\s*)?"
    r"(?:Stack: (?P<stack>[^.]+(?:\.[A-Za-z][^.]*)*)\.\s*)?"
    r"(?:Tools: (?P<tools>.+?)\.|The recorded source was (?P<sources>.+?)\.)?\s*$",
    re.DOTALL,
)
_TOOL_RE = re.compile(r"^(?P<name>.+?) \((?P<count>\d+)x\)$")


def project_slug(name: str) -> str:
    """Case-folded, filename- and tag-safe key: 'Gurukul' and 'gurukul' merge."""
    slug = _SLUG_BAD_RE.sub("-", name.strip().lower()).strip("-.")
    return slug or "unknown"


def clean_task(text: str) -> str:
    text = redact_sensitive_text(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > TASK_CHARS:
        text = text[: TASK_CHARS - 1].rstrip() + "…"
    return text


def post_summary(post_markdown: str) -> str:
    """Post body without the `# YYYY-MM-DD` heading."""
    lines = post_markdown.replace("\r\n", "\n").strip().split("\n")
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _ordered_tasks(messages: list[str]) -> list[str]:
    """Informative prompts first, deduplicated, harness noise removed."""
    kept = [m for m in messages if not is_injected_prompt(m)]
    informative = [m for m in kept if not is_low_signal_prompt(m)]
    out: list[str] = []
    for message in informative or kept[:1]:
        cleaned = clean_task(message)
        if cleaned and cleaned not in out:
            out.append(cleaned)
        if len(out) == MAX_TASKS:
            break
    return out


def _tokens(group: list[SessionDigest]) -> dict[str, int]:
    return {
        "in": sum(d.tokens_in for d in group),
        "out": sum(d.tokens_out for d in group),
        "cache": sum(d.tokens_cache_read for d in group),
    }


def _threads(group: list[SessionDigest]) -> list[str]:
    out: list[str] = []
    for d in sorted(group, key=lambda d: d.end_time):
        for thread in d.threads:
            cleaned = clean_task(thread)
            if cleaned and cleaned not in out:
                out.append(cleaned)
    return out[:MAX_THREADS]


def _notebooks(group: list[SessionDigest]) -> list[dict]:
    """Jupyter notebooks the sessions touched, for file:// links in the private vault."""
    paths = sorted({f for d in group for f in d.files_touched if f.lower().endswith(".ipynb")})
    return [{"name": redact_sensitive_text(basename(f)), "path": f.replace("\\", "/")}
            for f in paths[:MAX_NOTEBOOKS]]


def attach_runs(projects: list[dict], runs: list[dict]) -> list[str]:
    """Tag runs with their project slug; failed runs become open threads.

    A failed run's thread goes on its project when that project was active
    that day; otherwise it's returned as a day-level thread.
    """
    by_slug = {p["slug"]: p for p in projects}
    loose: list[str] = []
    for run in runs:
        run["project_slug"] = project_slug(run.pop("project", "") or "unknown")
        if run["status"] != "failed":
            continue
        thread = clean_task(run_thread(run))
        owner = by_slug.get(run["project_slug"])
        target = owner.setdefault("threads", []) if owner is not None else loose
        if thread not in target:
            target.append(thread)
    return loose


def build_day_meta(
    day: date,
    digests: list[SessionDigest],
    post_markdown: str,
    resolver: ProjectResolver | None = None,
) -> dict:
    """Metadata from the same session digests the post was generated from."""
    resolver = resolver or ProjectResolver()
    by_project: dict[str, list[SessionDigest]] = {}
    names: dict[str, Counter] = {}
    identities: dict[str, Project] = {}
    for digest in digests:
        project = resolver.resolve(digest.project_path)
        slug = project_slug(project.name)
        by_project.setdefault(slug, []).append(digest)
        names.setdefault(slug, Counter())[project.name] += 1
        identities.setdefault(slug, project)

    projects: list[dict] = []
    for slug, group in by_project.items():
        messages = [m for d in group for m in d.user_messages]
        tools: Counter = Counter()
        for d in group:
            tools.update(d.tool_calls)
        files = Counter(
            redact_sensitive_text(basename(f)) for d in group for f in d.files_touched
        )
        tasks = _ordered_tasks(messages)
        projects.append(
            {
                "slug": slug,
                "name": names[slug].most_common(1)[0][0],
                "minutes": round(total_active_minutes(group)),
                "sessions": len(group),
                "sources": sorted({d.source for d in group}),
                "work_types": classify(tasks, dict(tools)),
                "tasks": tasks,
                "files": [f for f, _ in files.most_common(MAX_FILES)],
                "tools": {k: v for k, v in tools.most_common(MAX_TOOLS)},
                "tokens": _tokens(group),
                "tokens_by_model": merge_tokens(*(d.tokens_by_model for d in group)),
                "threads": _threads(group),
                "repo_url": identities[slug].repo_url,
                "root": str(identities[slug].root) if identities[slug].root else None,
                "commits": resolver.commits(identities[slug], day),
                "pull_requests": resolver.pull_requests(identities[slug], day),
                "notebooks": _notebooks(group),
                "references": reference_dicts(
                    [m for m in messages if not is_injected_prompt(m)]),
            }
        )
    projects.sort(key=lambda p: (-p["minutes"], p["slug"]))
    search = [str(p.root) for p in identities.values() if p.root is not None]
    search += sorted({d.project_path for d in digests if d.project_path})
    runs = resolver.runs(search, day)
    return _finish(
        day,
        projects,
        post_markdown,
        minutes=round(total_active_minutes(digests)),
        sessions=len(digests),
        sources=sorted({d.source for d in digests}),
        origin="sessions",
        tokens=_tokens(digests),
        runs=runs,
    )


def parse_post_meta(
    day: date, post_markdown: str, resolver: ProjectResolver | None = None
) -> dict:
    """Best-effort metadata from a published post (template wording).

    Used for backfill when the original transcripts are gone. LLM-written
    posts don't follow the template, so they get work types from their prose
    and no project list.
    """
    summary = post_summary(post_markdown)
    match = _TEMPLATE_RE.search(re.sub(r"\s+", " ", summary))
    if match is None:
        return _finish(day, [], post_markdown, minutes=0, sessions=0, sources=[], origin="post",
                       runs=resolver.runs([], day) if resolver is not None else [])

    alias = resolver.alias if resolver is not None else (lambda n: n)
    raw_names = [alias(n.strip()) for n in (match.group("projects") or "").split(",")
                 if n.strip()]
    # "Work:" clause: per project ("x and y on name; ...") or day-wide ("x, y").
    work_by_slug: dict[str, list[str]] = {}
    day_work: list[str] = []
    for chunk in (match.group("work") or "").split("; "):
        kinds, sep, name = chunk.rpartition(" on ")
        if sep:
            work_by_slug[project_slug(alias(name))] = [
                w for w in kinds.split(" and ") if w in WORK_TYPE_DESCRIPTIONS]
        else:
            day_work += [w.strip() for w in chunk.split(",")
                         if w.strip() in WORK_TYPE_DESCRIPTIONS]
    tasks_by_slug: dict[str, list[str]] = {}
    for chunk in (match.group("tasks") or "").split("; "):
        name, sep, task = chunk.partition(": ")
        if sep and not is_injected_prompt(task):
            cleaned = clean_task(task)
            if cleaned and not is_low_signal_prompt(cleaned):
                tasks_by_slug.setdefault(project_slug(alias(name)), []).append(cleaned)

    tools: dict[str, int] = {}
    for item in (match.group("tools") or "").split(", "):
        tool = _TOOL_RE.match(item.strip())
        if tool:
            tools[tool.group("name")] = int(tool.group("count"))

    projects: list[dict] = []
    seen: set[str] = set()
    single = len(raw_names) == 1
    for name in raw_names:
        slug = project_slug(name)
        if slug in seen:
            continue
        seen.add(slug)
        tasks = tasks_by_slug.get(slug, [])
        # Tool counts are day-wide; only attribute them to a sole project.
        project_tools = tools if single else {}
        projects.append(
            {
                "slug": slug,
                "name": name,
                "minutes": int(match.group("minutes")) if single else None,
                "sessions": None,
                "sources": [],
                "work_types": work_by_slug.get(slug) or classify(tasks, project_tools),
                "tasks": tasks,
                "files": [],
                "tools": project_tools,
                "references": reference_dicts(tasks),
            }
        )
    sessions = match.group("sessions")
    sources = [s.strip() for s in (match.group("sources") or "").split(",") if s.strip()]
    runs = resolver.runs([], day) if resolver is not None else []
    if not projects and day_work:
        # summary-level post: no names, but the kinds of work are known.
        meta = _finish(day, [], post_markdown, minutes=int(match.group("minutes")),
                       sessions=None, sources=sources, origin="post", runs=runs)
        return {**meta, "work_types": day_work}
    return _finish(
        day,
        projects,
        post_markdown,
        minutes=int(match.group("minutes")),
        sessions=int(sessions) if sessions else None,
        sources=sources,
        origin="post",
        day_tools=tools,
        runs=runs,
    )


def _finish(
    day: date,
    projects: list[dict],
    post_markdown: str,
    *,
    minutes: int,
    sessions: int | None,
    sources: list[str],
    origin: str,
    day_tools: dict[str, int] | None = None,
    tokens: dict[str, int] | None = None,
    runs: list[dict] | None = None,
) -> dict:
    summary = post_summary(post_markdown)
    runs = list(runs or [])
    day_threads = attach_runs(projects, runs)
    work: list[str] = []
    for project in projects:
        for slug in project["work_types"]:
            if slug not in work:
                work.append(slug)
    if not work and projects:
        # Fall back to the prose itself (LLM-written or multi-project posts).
        work = classify([summary], day_tools)
    elif not projects and summary and not summary.startswith("No coding activity"):
        work = classify([summary])
    return {
        "date": day.isoformat(),
        "active_minutes": minutes,
        "sessions": sessions,
        "sources": sources,
        "work_types": work,
        "projects": projects,
        "summary": summary,
        "origin": origin,
        "tokens": tokens,
        "runs": runs,
        "threads": day_threads,
    }


def is_empty_day(meta: dict) -> bool:
    return not meta.get("projects") and not meta.get("active_minutes")


def headline(meta: dict) -> str:
    """One short line for timelines: the first project's first task, else the summary."""
    projects = meta.get("projects") or []
    for project in projects:
        task = headline_task(project.get("tasks") or [])
        if task:
            return task
    if projects:
        # Template prose repeats the numbers already shown next to the headline.
        return ""
    summary = meta.get("summary") or ""
    return clean_task(summary.split("\n")[0]) if summary else ""
