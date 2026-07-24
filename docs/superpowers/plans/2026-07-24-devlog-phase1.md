# Daily Dev Log Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the prototype into a multi-source plugin CLI that builds one factual daily build-log post from Claude Code sessions, with correct local-TZ day slicing, `cwd`-based paths, `--dry-run`, and a pytest suite.

**Architecture:** Source plugins parse on-disk transcripts into unsliced `RawSession` objects. Shared `digest.slice_for_date` clips each session to a local-timezone calendar day. `summarize` turns the day’s digests into a post (Claude API or template fallback). `cli` wires argparse, source registry, and file output.

**Tech Stack:** Python 3.11+, pytest, anthropic (optional at runtime), zoneinfo (stdlib)

## Global Constraints

- Day boundaries use the **local system timezone** (injectable `tz` in library code for tests)
- Day slicing lives **only** in `digest.py` — plugins never reimplement it
- Project path priority: event `cwd` → common root of tool paths → lossy folder decode
- Malformed JSONL lines are skipped; pipeline never crashes on summarization failure
- Default `--sources` is `claude_code`; Codex/Cursor are stubs returning `[]`
- Write `devlog-YYYY-MM-DD.md` unless `--dry-run`; always print the post
- Claude model: `claude-sonnet-4-6`; mocked summarization assert: 3–5 sentences / ≤120 words
- GitHub + landing page are **out of scope** for this plan
- Do not commit unless the user has asked for commits in this session; skip commit steps or stage only

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, pytest, deps |
| `devlog/__init__.py` | Package marker |
| `devlog/models.py` | `SessionEvent`, `RawSession`, `SessionDigest` |
| `devlog/digest.py` | `slice_for_date`, `build_raw_digest` |
| `devlog/summarize.py` | Template + Claude narration |
| `devlog/cli.py` | Argparse entrypoint |
| `devlog/sources/base.py` | `SourceParser` protocol + registry |
| `devlog/sources/claude_code.py` | Claude Code JSONL parser |
| `devlog/sources/codex.py` | Stub |
| `devlog/sources/cursor.py` | Stub |
| `main.py` | Thin `cli.main()` wrapper |
| `tests/` | pytest suite |
| `sample_data/claude_code/` | Fixtures (relocated from flat `sample_data/`) |
| Remove after migration | Root `claude_code_parser.py`, root `summarize.py` (logic moved) |

---

### Task 1: Scaffold package + pytest

**Files:**
- Create: `pyproject.toml`
- Create: `devlog/__init__.py`
- Create: `devlog/sources/__init__.py`
- Create: `tests/test_scaffold.py`
- Modify: `main.py` (temporary re-export until Task 8)

**Interfaces:**
- Produces: installable `devlog` package; `pytest` runnable

- [ ] **Step 1: Write failing scaffold test**

```python
# tests/test_scaffold.py
def test_package_imports():
    import devlog

    assert devlog is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scaffold.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'devlog'`

- [ ] **Step 3: Create package + pyproject**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "devlog"
version = "0.1.0"
description = "Daily build-log posts from AI coding tool session transcripts"
requires-python = ">=3.11"
dependencies = [
  "anthropic>=0.40.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
devlog = "devlog.cli:main"

[tool.setuptools.packages.find]
include = ["devlog*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

```python
# devlog/__init__.py
"""Daily Dev Log — local CLI for AI coding session digests."""

__version__ = "0.1.0"
```

```python
# devlog/sources/__init__.py
```

- [ ] **Step 4: Install editable + run test**

Run: `python -m pip install -e ".[dev]"` then `python -m pytest tests/test_scaffold.py -v`

Expected: PASS

- [ ] **Step 5: Init git if missing (no commit unless user asked)**

Run: `git status` — if not a repo, `git init` only. Do not commit yet.

---

### Task 2: Core models

**Files:**
- Create: `devlog/models.py`
- Create: `tests/test_models.py`
- Delete: `tests/test_scaffold.py` (optional; or leave)

**Interfaces:**
- Produces:
  - `SessionEvent(timestamp, user_message=None, tool_name=None, file_path=None, bash_command=None, tokens_in=0, tokens_out=0, tokens_cache_read=0)`
  - `RawSession(session_id, project_path, source, start_time, end_time, events: list[SessionEvent])`
  - `SessionDigest` with fields from the design + `duration_minutes` property

- [ ] **Step 1: Write failing model tests**

```python
# tests/test_models.py
from datetime import datetime, timezone
from devlog.models import SessionEvent, RawSession, SessionDigest


def test_session_digest_duration_minutes():
    start = datetime(2026, 7, 22, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 22, 9, 30, tzinfo=timezone.utc)
    d = SessionDigest(
        session_id="s1",
        project_path="/proj",
        source="claude_code",
        start_time=start,
        end_time=end,
    )
    assert d.duration_minutes == 30.0


def test_raw_session_holds_events():
    ts = datetime(2026, 7, 22, 9, 0, tzinfo=timezone.utc)
    ev = SessionEvent(timestamp=ts, user_message="fix parser")
    raw = RawSession(
        session_id="s1",
        project_path="/proj",
        source="claude_code",
        start_time=ts,
        end_time=ts,
        events=[ev],
    )
    assert raw.events[0].user_message == "fix parser"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_models.py -v`

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `devlog.models`

- [ ] **Step 3: Implement models**

```python
# devlog/models.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SessionEvent:
    timestamp: datetime
    user_message: str | None = None
    tool_name: str | None = None
    file_path: str | None = None
    bash_command: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache_read: int = 0


@dataclass
class RawSession:
    session_id: str
    project_path: str
    source: str
    start_time: datetime
    end_time: datetime
    events: list[SessionEvent] = field(default_factory=list)


@dataclass
class SessionDigest:
    session_id: str
    project_path: str
    source: str
    start_time: datetime
    end_time: datetime
    user_messages: list[str] = field(default_factory=list)
    tool_calls: dict[str, int] = field(default_factory=dict)
    files_touched: set[str] = field(default_factory=set)
    bash_commands: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache_read: int = 0

    @property
    def duration_minutes(self) -> float:
        return max(0.0, (self.end_time - self.start_time).total_seconds() / 60.0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_models.py -v`

Expected: PASS

---

### Task 3: Day slicing (`digest.slice_for_date`)

**Files:**
- Create: `devlog/digest.py`
- Create: `tests/test_digest.py`

**Interfaces:**
- Consumes: `RawSession`, `SessionEvent`, `SessionDigest`
- Produces:
  - `day_bounds(target_date: date, tz: tzinfo) -> tuple[datetime, datetime]`
  - `slice_for_date(sessions: list[RawSession], target_date: date, tz: tzinfo) -> list[SessionDigest]`
  - `build_raw_digest(sessions: list[SessionDigest]) -> str`

- [ ] **Step 1: Write failing midnight / TZ tests**

```python
# tests/test_digest.py
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from devlog.models import RawSession, SessionEvent
from devlog.digest import slice_for_date, build_raw_digest

IST = ZoneInfo("Asia/Kolkata")


def _ev(ts: datetime, **kwargs) -> SessionEvent:
    return SessionEvent(timestamp=ts, **kwargs)


def test_midnight_spanning_session_splits_without_double_count():
    # Session 23:30 IST day1 -> 00:30 IST day2
    start = datetime(2026, 7, 22, 23, 30, tzinfo=IST)
    mid = datetime(2026, 7, 23, 0, 10, tzinfo=IST)
    end = datetime(2026, 7, 23, 0, 30, tzinfo=IST)
    raw = RawSession(
        session_id="span1",
        project_path="/proj",
        source="claude_code",
        start_time=start,
        end_time=end,
        events=[
            _ev(start, user_message="before midnight"),
            _ev(
                start + timedelta(minutes=5),
                tool_name="Edit",
                file_path="/proj/a.py",
                tokens_in=100,
                tokens_out=50,
            ),
            _ev(mid, user_message="after midnight"),
            _ev(end, tool_name="Bash", bash_command="pytest", tokens_in=40, tokens_out=20),
        ],
    )
    day1 = slice_for_date([raw], date(2026, 7, 22), IST)
    day2 = slice_for_date([raw], date(2026, 7, 23), IST)
    assert len(day1) == 1 and len(day2) == 1
    assert day1[0].user_messages == ["before midnight"]
    assert day2[0].user_messages == ["after midnight"]
    assert day1[0].tokens_in == 100
    assert day2[0].tokens_in == 40
    assert abs(day1[0].duration_minutes + day2[0].duration_minutes - 60.0) < 0.01


def test_no_overlap_returns_empty():
    ts = datetime(2026, 7, 21, 12, 0, tzinfo=IST)
    raw = RawSession(
        session_id="s",
        project_path="/p",
        source="claude_code",
        start_time=ts,
        end_time=ts,
        events=[_ev(ts, user_message="old")],
    )
    assert slice_for_date([raw], date(2026, 7, 22), IST) == []


def test_build_raw_digest_lists_projects():
    start = datetime(2026, 7, 22, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 22, 9, 15, tzinfo=timezone.utc)
    from devlog.models import SessionDigest

    d = SessionDigest(
        session_id="s",
        project_path="/Users/dev/code/variantgpt",
        source="claude_code",
        start_time=start,
        end_time=end,
        user_messages=["Refactor VCF parser"],
        tool_calls={"Edit": 1},
        files_touched={"/Users/dev/code/variantgpt/parsers/vcf_parser.py"},
    )
    text = build_raw_digest([d])
    assert "variantgpt" in text
    assert "Refactor VCF parser" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_digest.py -v`

Expected: FAIL — `devlog.digest` missing

- [ ] **Step 3: Implement digest.py**

```python
# devlog/digest.py
from __future__ import annotations
from datetime import date, datetime, time, timedelta, tzinfo

from devlog.models import RawSession, SessionDigest, SessionEvent


def day_bounds(target_date: date, tz: tzinfo) -> tuple[datetime, datetime]:
    start = datetime.combine(target_date, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


def slice_for_date(
    sessions: list[RawSession],
    target_date: date,
    tz: tzinfo,
) -> list[SessionDigest]:
    day_start, day_end = day_bounds(target_date, tz)
    out: list[SessionDigest] = []
    for raw in sessions:
        # Normalize event times to tz for comparison
        events = [e for e in raw.events if day_start <= e.timestamp.astimezone(tz) < day_end]
        sess_start = raw.start_time.astimezone(tz)
        sess_end = raw.end_time.astimezone(tz)
        if sess_end <= day_start or sess_start >= day_end:
            continue
        clipped_start = max(sess_start, day_start)
        clipped_end = min(sess_end, day_end)
        if clipped_end <= clipped_start and not events:
            continue
        digest = SessionDigest(
            session_id=raw.session_id,
            project_path=raw.project_path,
            source=raw.source,
            start_time=clipped_start,
            end_time=clipped_end if clipped_end > clipped_start else clipped_start,
        )
        for e in events:
            if e.user_message:
                digest.user_messages.append(e.user_message)
            if e.tool_name:
                digest.tool_calls[e.tool_name] = digest.tool_calls.get(e.tool_name, 0) + 1
            if e.file_path:
                digest.files_touched.add(e.file_path)
            if e.bash_command:
                digest.bash_commands.append(e.bash_command)
            digest.tokens_in += e.tokens_in
            digest.tokens_out += e.tokens_out
            digest.tokens_cache_read += e.tokens_cache_read
        out.append(digest)
    return sorted(out, key=lambda s: s.start_time)


def build_raw_digest(sessions: list[SessionDigest]) -> str:
    if not sessions:
        return "No coding activity recorded today."
    lines: list[str] = []
    total_minutes = sum(s.duration_minutes for s in sessions)
    projects = sorted({s.project_path for s in sessions})
    lines.append(
        f"Total active time: {total_minutes:.0f} minutes across {len(sessions)} session(s)."
    )
    lines.append(f"Projects touched: {', '.join(projects)}")
    for s in sessions:
        lines.append(
            f"\n[Project: {s.project_path}, {s.duration_minutes:.0f} min, source={s.source}]"
        )
        if s.user_messages:
            lines.append("  Tasks requested: " + " | ".join(s.user_messages))
        if s.tool_calls:
            tool_summary = ", ".join(f"{k} x{v}" for k, v in s.tool_calls.items())
            lines.append(f"  Tools used: {tool_summary}")
        if s.files_touched:
            lines.append(f"  Files touched: {', '.join(sorted(s.files_touched))}")
        if s.bash_commands:
            lines.append(f"  Commands run: {', '.join(s.bash_commands)}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_digest.py -v`

Expected: PASS (adjust clip edge cases if duration assert is off by a minute — keep non-overlapping invariant)

---

### Task 4: Source registry + stubs

**Files:**
- Create: `devlog/sources/base.py`
- Create: `devlog/sources/codex.py`
- Create: `devlog/sources/cursor.py`
- Create: `tests/test_sources_registry.py`

**Interfaces:**
- Produces:
  - `class SourceParser(Protocol): name: str; def iter_sessions(self, root: Path) -> list[RawSession]`
  - `REGISTRY: dict[str, SourceParser]`
  - `get_sources(names: list[str]) -> list[SourceParser]` — raises `KeyError` with known names
  - Codex/Cursor stubs with `iter_sessions` → `[]`

- [ ] **Step 1: Write failing registry tests**

```python
# tests/test_sources_registry.py
from pathlib import Path
import pytest
from devlog.sources.base import get_sources, REGISTRY


def test_stubs_registered():
    assert "codex" in REGISTRY
    assert "cursor" in REGISTRY


def test_stubs_return_empty(tmp_path: Path):
    assert get_sources(["codex"])[0].iter_sessions(tmp_path) == []
    assert get_sources(["cursor"])[0].iter_sessions(tmp_path) == []


def test_unknown_source_raises():
    with pytest.raises(KeyError) as exc:
        get_sources(["nope"])
    assert "claude_code" in str(exc.value) or "known" in str(exc.value).lower()
```

- [ ] **Step 2: Run tests — expect fail**

Run: `python -m pytest tests/test_sources_registry.py -v`

Expected: FAIL — missing modules

- [ ] **Step 3: Implement base + stubs**

```python
# devlog/sources/base.py
from __future__ import annotations
from pathlib import Path
from typing import Protocol

from devlog.models import RawSession


class SourceParser(Protocol):
    name: str

    def iter_sessions(self, root: Path) -> list[RawSession]: ...


REGISTRY: dict[str, SourceParser] = {}


def register(parser: SourceParser) -> SourceParser:
    REGISTRY[parser.name] = parser
    return parser


def get_sources(names: list[str]) -> list[SourceParser]:
    missing = [n for n in names if n not in REGISTRY]
    if missing:
        known = ", ".join(sorted(REGISTRY)) or "(none yet)"
        raise KeyError(f"Unknown source(s): {', '.join(missing)}. Known: {known}")
    return [REGISTRY[n] for n in names]
```

```python
# devlog/sources/codex.py
from __future__ import annotations
from pathlib import Path
from devlog.models import RawSession
from devlog.sources.base import register


class CodexParser:
    name = "codex"

    def iter_sessions(self, root: Path) -> list[RawSession]:
        return []


register(CodexParser())
```

```python
# devlog/sources/cursor.py
from __future__ import annotations
from pathlib import Path
from devlog.models import RawSession
from devlog.sources.base import register


class CursorParser:
    name = "cursor"

    def iter_sessions(self, root: Path) -> list[RawSession]:
        return []


register(CursorParser())
```

Update `devlog/sources/__init__.py` to import stubs (and later claude):

```python
from devlog.sources import codex as _codex  # noqa: F401
from devlog.sources import cursor as _cursor  # noqa: F401
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_sources_registry.py -v`

Expected: PASS

---

### Task 5: Claude Code parser (cwd, malformed lines, path fallback)

**Files:**
- Create: `devlog/sources/claude_code.py`
- Create: `tests/test_claude_code_parser.py`
- Create: `sample_data/claude_code/` fixtures (move + add cwd / midnight / malformed)
- Modify: `devlog/sources/__init__.py` to import claude_code

**Interfaces:**
- Consumes: `RawSession`, `SessionEvent`, `register`
- Produces: `ClaudeCodeParser` with `name="claude_code"`, `iter_sessions(root)` scanning `root/projects/*/*.jsonl` (or `root/*/*.jsonl` when `sample_mode` layout uses projects-as-root — see CLI flag)
- Path helpers: `decode_project_path`, `resolve_project_path(cwd, files, folder_name)`

**Fixture layout after move:**
```
sample_data/claude_code/projects/-Users-dev-code-variantgpt/session-a1b2c3d4.jsonl
sample_data/claude_code/projects/-Users-dev-code-helios-patent/session-e5f6a7b8.jsonl
sample_data/claude_code/projects/-Users-dev-code-variant-caller/session-cwd.jsonl   # has cwd
sample_data/claude_code/projects/-Users-dev-code-span/session-midnight.jsonl
sample_data/claude_code/projects/-Users-dev-code-bad/session-malformed.jsonl
```

- [ ] **Step 1: Relocate existing sample sessions under `sample_data/claude_code/projects/`**

Move the two existing project folders; keep JSONL contents. Delete empty old `sample_data/-Users-...` dirs.

- [ ] **Step 2: Write failing parser tests**

```python
# tests/test_claude_code_parser.py
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from devlog.sources.claude_code import ClaudeCodeParser, resolve_project_path
from devlog.digest import slice_for_date

FIXTURES = Path(__file__).resolve().parents[1] / "sample_data" / "claude_code"


def test_cwd_preferred_over_folder_decode(tmp_path: Path):
    proj = tmp_path / "projects" / "-Users-dev-code-variant-caller"
    proj.mkdir(parents=True)
    session = proj / "s1.jsonl"
    lines = [
        {
            "type": "user",
            "cwd": "/Users/dev/code/variant-caller",
            "timestamp": "2026-07-22T10:00:00Z",
            "message": {"role": "user", "content": "Add tests"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-07-22T10:01:00Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "/Users/dev/code/variant-caller/main.py"},
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0},
            },
        },
    ]
    session.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    raw = ClaudeCodeParser().iter_sessions(tmp_path)
    assert len(raw) == 1
    assert raw[0].project_path == "/Users/dev/code/variant-caller"


def test_malformed_line_skipped(tmp_path: Path):
    proj = tmp_path / "projects" / "-Users-dev-code-x"
    proj.mkdir(parents=True)
    (proj / "s.jsonl").write_text(
        '{"type":"user","timestamp":"2026-07-22T10:00:00Z","message":{"content":"ok"}}\n'
        "NOT JSON\n"
        '{"type":"assistant","timestamp":"2026-07-22T10:01:00Z","message":{"content":[],"usage":{"input_tokens":1,"output_tokens":1,"cache_read_input_tokens":0}}}\n',
        encoding="utf-8",
    )
    raw = ClaudeCodeParser().iter_sessions(tmp_path)
    assert len(raw) == 1
    assert raw[0].events[0].user_message == "ok"


def test_resolve_path_fallback_order():
    assert (
        resolve_project_path(
            cwd="/real/variant-caller",
            files=["/real/variant-caller/a.py"],
            folder_name="-Users-dev-code-variant-caller",
        )
        == "/real/variant-caller"
    )
```

- [ ] **Step 3: Run tests — expect fail**

Run: `python -m pytest tests/test_claude_code_parser.py -v`

Expected: FAIL — module missing

- [ ] **Step 4: Implement `claude_code.py`**

Port logic from root `claude_code_parser.py`, but:

1. Emit `RawSession` + `SessionEvent` (not day-sliced digests)
2. Read `cwd` from each event if present; keep first non-empty
3. Attach tool tokens onto the assistant event that carried them
4. Register via `register(ClaudeCodeParser())`
5. `iter_sessions(root)` looks for `root/projects` if it exists, else treats `root` as the projects dir (for flexibility)

```python
# Key signatures to implement:
def decode_project_path(encoded_dir_name: str) -> str: ...
def resolve_project_path(cwd: str | None, files: list[str], folder_name: str) -> str: ...
def parse_session_file(path: Path) -> RawSession | None: ...


class ClaudeCodeParser:
    name = "claude_code"

    def iter_sessions(self, root: Path) -> list[RawSession]: ...
```

Full implementation should mirror existing parsing of user/assistant/tool_use fields from the prototype, plus `cwd` collection. Skip empty/corrupt files (`None`).

- [ ] **Step 5: Wire import in `devlog/sources/__init__.py`**

```python
from devlog.sources import claude_code as _claude_code  # noqa: F401
from devlog.sources import codex as _codex  # noqa: F401
from devlog.sources import cursor as _cursor  # noqa: F401
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_claude_code_parser.py tests/test_sources_registry.py -v`

Expected: PASS; `claude_code` now in `REGISTRY`

---

### Task 6: Summarization (template + mocked Claude)

**Files:**
- Create: `devlog/summarize.py` (port from root `summarize.py`)
- Create: `tests/test_summarize.py`

**Interfaces:**
- Consumes: `SessionDigest`, `build_raw_digest`
- Produces:
  - `SUMMARY_SYSTEM_PROMPT: str`
  - `summarize_with_template(sessions: list[SessionDigest]) -> str`
  - `summarize_with_claude(raw_digest: str, api_key: str | None = None) -> str`
  - `generate_post(sessions: list[SessionDigest]) -> str`

- [ ] **Step 1: Write failing summarize tests**

```python
# tests/test_summarize.py
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from devlog.models import SessionDigest
from devlog.summarize import generate_post, summarize_with_template


def _sess() -> SessionDigest:
    return SessionDigest(
        session_id="s",
        project_path="/Users/dev/code/variantgpt",
        source="claude_code",
        start_time=datetime(2026, 7, 22, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 22, 9, 20, tzinfo=timezone.utc),
        user_messages=["Refactor the VCF parser"],
        tool_calls={"Edit": 2, "Bash": 1},
        files_touched={"/Users/dev/code/variantgpt/parsers/vcf_parser.py"},
    )


def test_template_is_stable_and_factual():
    text = summarize_with_template([_sess()])
    assert "variantgpt" in text
    assert "Refactor the VCF parser" in text
    assert "!" not in text


def test_generate_post_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    text = generate_post([_sess()])
    assert "variantgpt" in text


def test_generate_post_uses_claude_when_key_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake = "I spent time on variantgpt refactoring the VCF parser to stream. Edited the parser module and ran pytest. Also sketched a progress bar for large files."
    with patch("devlog.summarize.summarize_with_claude", return_value=fake) as mocked:
        text = generate_post([_sess()])
    mocked.assert_called_once()
    assert text == fake
    assert len(text.split()) <= 120
    assert 3 <= text.count(".") <= 5 or 3 <= len([s for s in text.split(".") if s.strip()]) <= 5


def test_claude_failure_falls_back(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch("devlog.summarize.summarize_with_claude", side_effect=RuntimeError("boom")):
        text = generate_post([_sess()])
    assert "variantgpt" in text
```

- [ ] **Step 2: Run — expect fail**

Run: `python -m pytest tests/test_summarize.py -v`

Expected: FAIL

- [ ] **Step 3: Implement `devlog/summarize.py`**

Port from root `summarize.py`; import `build_raw_digest` from `devlog.digest` (remove duplicate). Keep `SUMMARY_SYSTEM_PROMPT`, model `claude-sonnet-4-6`, try/except fallback in `generate_post`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_summarize.py -v`

Expected: PASS

---

### Task 7: CLI (`--dry-run`, local TZ, sources)

**Files:**
- Create: `devlog/cli.py`
- Create: `tests/test_cli.py`
- Modify: `main.py` → call `devlog.cli.main`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> int`
- Flags per design: `--date`, `--sources`, `--claude-root`, `--dry-run`, `--verbose`, `--sample-mode`
- Local TZ: `datetime.now().astimezone().tzinfo` for interpreting `today` and slicing
- Exit codes: `0` success/no activity; `2` unknown source

- [ ] **Step 1: Write failing CLI tests**

```python
# tests/test_cli.py
from pathlib import Path
from devlog.cli import main


def test_dry_run_does_not_write(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "claude_code"
    code = main(
        [
            "--date",
            "2026-07-22",
            "--claude-root",
            str(sample),
            "--sample-mode",
            "--dry-run",
        ]
    )
    assert code == 0
    assert list(tmp_path.glob("devlog-*.md")) == []
    out = capsys.readouterr().out
    assert "Daily post" in out or "variantgpt" in out.lower() or "session" in out.lower()


def test_write_creates_file(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "claude_code"
    code = main(
        [
            "--date",
            "2026-07-22",
            "--claude-root",
            str(sample),
            "--sample-mode",
        ]
    )
    assert code == 0
    assert (tmp_path / "devlog-2026-07-22.md").exists()


def test_unknown_source_exits_2():
    code = main(["--sources", "nope", "--dry-run", "--date", "2026-07-22"])
    assert code == 2
```

- [ ] **Step 2: Run — expect fail**

Run: `python -m pytest tests/test_cli.py -v`

Expected: FAIL

- [ ] **Step 3: Implement `devlog/cli.py`**

```python
# Outline — full code in implementation:
# - parse args
# - resolve target_date (today -> local date)
# - tz = datetime.now().astimezone().tzinfo
# - import sources package for side-effect registration
# - get_sources(names); on KeyError print + return 2
# - for each source, resolve root (claude_code -> --claude-root; sample-mode uses that path)
# - collect RawSessions; slice_for_date; generate_post; print; maybe write
# - missing root: message + return 0
```

```python
# main.py
from devlog.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run CLI tests**

Run: `python -m pytest tests/test_cli.py -v`

Expected: PASS

- [ ] **Step 5: Manual sample smoke**

Run: `python main.py --date 2026-07-22 --claude-root sample_data/claude_code --sample-mode --dry-run`

Expected: prints a post; no new `devlog-*.md` in cwd (or only if not dry-run)

---

### Task 8: Remove prototype modules + update README

**Files:**
- Delete: `claude_code_parser.py`, root `summarize.py` (logic now in package)
- Modify: `README.md` to document plugin layout, flags, pytest
- Keep: `PRD_TRD.md`, design/plan docs

- [ ] **Step 1: Delete obsolete root modules** after confirming imports only use `devlog.*`

- [ ] **Step 2: Update README** with:
  - `pip install -e ".[dev]"`
  - `python main.py --date today --dry-run`
  - `python main.py --date 2026-07-22 --claude-root sample_data/claude_code --sample-mode`
  - `python -m pytest`
  - Note: Codex/Cursor stubs; GitHub landing page next

- [ ] **Step 3: Full test suite**

Run: `python -m pytest -v`

Expected: all PASS

- [ ] **Step 4: Manual acceptance checklist (user)**

- Run against real `~/.claude` for ≥1 day with `--dry-run`
- Confirm project paths look right (not garbled dashes)
- Confirm `--date today` matches local calendar day

---

## Spec coverage checklist

| Spec requirement | Task |
|---|---|
| Plugin architecture + stubs | 4, 5 |
| `cwd` path priority | 5 |
| Local system timezone days | 3, 7 |
| Midnight slicing, no double-count | 3 |
| Template + Claude fallback | 6 |
| `--dry-run` | 7 |
| Malformed JSONL tolerance | 5 |
| Missing root → exit 0 | 7 |
| Unknown source → exit 2 | 7 |
| pytest suite | 2–7 |
| GitHub/landing deferred | Global constraints |

---

## Self-review notes

- No TBD placeholders in tasks
- `build_raw_digest` lives in `digest.py` (single owner); summarize imports it
- `RawSession` vs `SessionDigest` naming consistent across tasks
- Commit steps omitted per user git rule unless user later asks to commit
