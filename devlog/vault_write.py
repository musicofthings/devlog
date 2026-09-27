"""Opt-in writes to the vault for coding agents (`mcp_write = true`).

Three operations, each doing only what you could do by hand in Obsidian:

- close_thread: tick an open thread's checkbox in its day note and project
  hub, exactly like clicking it; the next refresh records it as done.
- add_note: append a note under the user-owned part of a day note or
  project hub (below `%% devlog:end %%`), which regeneration never touches.
- log_decision: append a dated decision callout to a project hub's
  "## Decisions" section, also below the managed block.

Agent text is untrusted: it's length-capped, can't open Obsidian comments
(`%%`) or fake devlog markers, and every write is appended to
`.devlog/agent-writes.jsonl` so you can see what changed and when.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from devlog.config import DevlogConfig
from devlog.memory import VaultMemory
from devlog.vault_graph import (
    _CHECKBOX_RE,
    DEFAULT_TAIL,
    END,
    START,
    Graph,
    load_index,
    load_state,
    safe_text,
    thread_key,
)

MAX_TEXT = 2000
MAX_TITLE = 120
_WS_RE = re.compile(r"[ \t]+")


def clean_agent_text(text: str, limit: int = MAX_TEXT) -> str:
    """Untrusted text made safe to append to a note."""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(START, "").replace(END, "").replace("%%", "%")
    lines = [_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _append_to_section(text: str, heading: str, entry: str, *, separate: bool = False) -> str:
    """Add `entry` at the end of the `heading` section of the user-owned tail.

    `separate` puts a blank line before it (callouts would otherwise merge).
    """
    head, sep, tail = text.partition(END)
    if not sep:  # not a devlog note; treat the whole thing as the tail
        head, tail = "", text
    lines = tail.split("\n")
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        tail = tail.rstrip("\n") + f"\n\n{heading}\n\n{entry}\n"
        return head + sep + tail
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
               len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    block = entry.rstrip("\n").split("\n")
    if end == start + 1 or separate:
        block = ["", *block]
    lines[end:end] = block
    out = "\n".join(lines)
    return head + sep + (out if out.endswith("\n") else out + "\n")


class VaultWriter:
    def __init__(self, cfg: DevlogConfig, *, now=datetime.now) -> None:
        self.cfg = cfg
        self.memory = VaultMemory(cfg)
        self.root = self.memory.root
        self.now = now
        vault = Path(cfg.obsidian_vault).expanduser()
        self.graph = Graph(vault, (cfg.obsidian_folder or "DevLog").strip().strip("/\\"))

    # ------------------------------------------------------------ helpers

    def _days(self) -> dict[str, dict]:
        if self.root is None or not self.root.is_dir():
            return {}
        return load_index(self.root)

    def _log(self, tool: str, target: Path, summary: str) -> None:
        assert self.root is not None
        log = self.root / ".devlog" / "agent-writes.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        entry = {"at": self.now().isoformat(timespec="seconds"), "tool": tool,
                 "note": target.relative_to(self.graph.vault).as_posix(), "summary": summary}
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _project(self, days: dict[str, dict], project: str) -> tuple[str, Path] | None:
        slug = self.memory._resolve_project(days, project)
        if slug is None:
            return None
        hub = self.graph.path(self.graph.project_rel(slug))
        return (slug, hub) if hub.is_file() else None

    # ------------------------------------------------------------ tools

    def close_thread(self, thread: str, project: str | None = None) -> str:
        days = self._days()
        if not days:
            return "The devlog vault is empty."
        done = load_state(self.root)["done_threads"] if self.root else set()
        slug = None
        if project:
            slug = self.memory._resolve_project(days, project)
            if slug is None:
                return f"No project matching {project!r}."
        want = thread_key(safe_text(clean_agent_text(thread, 400)))
        if not want:
            return "Say which thread to close."
        candidates: dict[str, tuple[str, str, str | None]] = {}
        for d in sorted(days, reverse=True):
            meta = days[d]
            owned = [(p["slug"], t) for p in meta.get("projects") or []
                     for t in p.get("threads") or []]
            owned += [(None, t) for t in meta.get("threads") or []]
            for owner, t in owned:
                if slug is not None and owner != slug:
                    continue
                key = thread_key(safe_text(t))
                if key in done or key in candidates:
                    continue
                if want == key or want in key:
                    candidates[key] = (d, t, owner)
        exact = {k: v for k, v in candidates.items() if k == want}
        matches = exact or candidates
        if not matches:
            return f"No open thread matches {thread!r}."
        if len(matches) > 1:
            listed = "\n".join(f"- {t} ({d})" for d, t, _ in list(matches.values())[:6])
            return f"{len(matches)} open threads match; quote one exactly:\n{listed}"
        key, (day, text, owner) = next(iter(matches.items()))
        notes = [self.graph.path(self.graph.day_rel(day))]
        if owner:
            notes.append(self.graph.path(self.graph.project_rel(owner)))
        ticked: list[Path] = []
        for note in notes:
            if note.is_file() and self._tick(note, key):
                ticked.append(note)
        if not ticked:
            return f"Found {text!r} ({day}) but its checkbox isn't in the notes; refresh the vault."
        from devlog.obsidian import refresh_vault

        refresh_vault(self.cfg)
        self._log("close_thread", ticked[0], text)
        return f"Closed: {text} ({day})"

    @staticmethod
    def _tick(note: Path, key: str) -> bool:
        text = note.read_text(encoding="utf-8")
        start, end = text.find(START), text.find(END)
        if start == -1 or end == -1:
            return False
        lines = text[start:end].split("\n")
        changed = False
        for i, line in enumerate(lines):
            match = _CHECKBOX_RE.match(line)
            if match and match.group("mark") == " " and thread_key(match.group("text")) == key:
                lines[i] = line.replace("[ ]", "[x]", 1)
                changed = True
        if changed:
            note.write_text(text[:start] + "\n".join(lines) + text[end:], encoding="utf-8")
        return changed

    def add_note(self, text: str, day: str | None = None, project: str | None = None) -> str:
        days = self._days()
        if not days:
            return "The devlog vault is empty."
        body = clean_agent_text(text)
        if not body:
            return "The note is empty."
        stamp = self.now().strftime("%Y-%m-%d %H:%M")
        target: Path | None = None
        if day is not None or project is None:
            when = day or self.now().date().isoformat()
            try:
                date.fromisoformat(when)
            except ValueError:
                return f"Invalid date {when!r}: expected YYYY-MM-DD."
            note = self.graph.path(self.graph.day_rel(when))
            if note.is_file():
                target = note
            elif project is None:
                latest = max(days)
                return (f"No day note for {when} yet (it's written when the day is published). "
                        f"Pass a project to add it to the hub, or a date like {latest}.")
        if target is None:
            assert project is not None
            found = self._project(days, project)
            if found is None:
                return f"No project hub matching {project!r}."
            target = found[1]
        lines = body.split("\n")
        entry = "\n".join([f"- {stamp} (agent) {lines[0]}", *(f"  {ln}" for ln in lines[1:])])
        current = target.read_text(encoding="utf-8")
        if END not in current:
            current = current.rstrip("\n") + "\n" + END + DEFAULT_TAIL
        target.write_text(_append_to_section(current, "## Notes", entry), encoding="utf-8")
        self._log("add_note", target, lines[0][:120])
        return f"Added a note to {target.relative_to(self.graph.vault).as_posix()}."

    def log_decision(self, project: str, title: str, decision: str,
                     why: str | None = None) -> str:
        days = self._days()
        if not days:
            return "The devlog vault is empty."
        found = self._project(days, project)
        if found is None:
            return f"No project hub matching {project!r}."
        title = clean_agent_text(title, MAX_TITLE).replace("\n", " ")
        decision = clean_agent_text(decision)
        if not title or not decision:
            return "A decision needs a title and the decision itself."
        stamp = self.now().date().isoformat()
        block = [f"> [!decision] {stamp} · {title}"]
        block += [f"> {ln}" if ln else ">" for ln in f"**Decision:** {decision}".split("\n")]
        if why:
            block += [">"] + [f"> {ln}" if ln else ">"
                              for ln in f"**Why:** {clean_agent_text(why)}".split("\n")]
        hub = found[1]
        text = hub.read_text(encoding="utf-8")
        entry = "\n".join(block) + "\n"
        hub.write_text(_append_to_section(text, "## Decisions", entry, separate=True),
                       encoding="utf-8")
        self._log("log_decision", hub, title)
        return f"Logged decision '{title}' on {hub.relative_to(self.graph.vault).as_posix()}."
