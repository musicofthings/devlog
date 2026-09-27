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
from devlog.models import SessionDigest
from devlog.noise import headline_task, is_injected_prompt, is_low_signal_prompt
from devlog.privacy import redact_sensitive_text
from devlog.worktypes import classify

MAX_TASKS = 5
MAX_FILES = 8
MAX_TOOLS = 5
TASK_CHARS = 160

_SLUG_BAD_RE = re.compile(r"[^a-z0-9._-]+")
_TEMPLATE_RE = re.compile(
    r"Today I logged (?P<minutes>\d+) active min across (?P<projects>.+?)\.\s+"
    r"(?:I worked on (?P<tasks>.+?)\.\s+)?"
    r"(?:I recorded activity in (?P<sessions>\d+) coding session\(s\)\.\s+)?"
    r"(?:Tools: (?P<tools>.+?)\.|The recorded source was (?P<sources>.+?)\.)\s*$",
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


def build_day_meta(day: date, digests: list[SessionDigest], post_markdown: str) -> dict:
    """Metadata from the same session digests the post was generated from."""
    by_project: dict[str, list[SessionDigest]] = {}
    names: dict[str, Counter] = {}
    for digest in digests:
        name = redact_sensitive_text(basename(digest.project_path))
        slug = project_slug(name)
        by_project.setdefault(slug, []).append(digest)
        names.setdefault(slug, Counter())[name] += 1

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
            }
        )
    projects.sort(key=lambda p: (-p["minutes"], p["slug"]))
    return _finish(
        day,
        projects,
        post_markdown,
        minutes=round(total_active_minutes(digests)),
        sessions=len(digests),
        sources=sorted({d.source for d in digests}),
        origin="sessions",
    )


def parse_post_meta(day: date, post_markdown: str) -> dict:
    """Best-effort metadata from a published post (template wording).

    Used for backfill when the original transcripts are gone. LLM-written
    posts don't follow the template, so they get work types from their prose
    and no project list.
    """
    summary = post_summary(post_markdown)
    match = _TEMPLATE_RE.search(re.sub(r"\s+", " ", summary))
    if match is None:
        return _finish(day, [], post_markdown, minutes=0, sessions=0, sources=[], origin="post")

    raw_names = [n.strip() for n in match.group("projects").split(",") if n.strip()]
    tasks_by_slug: dict[str, list[str]] = {}
    for chunk in (match.group("tasks") or "").split("; "):
        name, sep, task = chunk.partition(": ")
        if sep and not is_injected_prompt(task):
            cleaned = clean_task(task)
            if cleaned and not is_low_signal_prompt(cleaned):
                tasks_by_slug.setdefault(project_slug(name), []).append(cleaned)

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
                "work_types": classify(tasks, project_tools),
                "tasks": tasks,
                "files": [],
                "tools": project_tools,
            }
        )
    sessions = match.group("sessions")
    sources = [s.strip() for s in (match.group("sources") or "").split(",") if s.strip()]
    return _finish(
        day,
        projects,
        post_markdown,
        minutes=int(match.group("minutes")),
        sessions=int(sessions) if sessions else None,
        sources=sources,
        origin="post",
        day_tools=tools,
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
) -> dict:
    summary = post_summary(post_markdown)
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
