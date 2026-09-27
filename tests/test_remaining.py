"""Remaining roadmap items: threads everywhere, identity in posts, richer posts,
quarterly rollups, hub README/PRs, cost, embeddings, retros."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SAMPLES = Path(__file__).resolve().parents[1] / "sample_data"
RECAP = "Done with the parser.\n\n## Next steps\n- add fixtures\n- wire into config\n"


def _threads(session) -> list[list[str]]:
    return [e.threads for e in session.events if e.threads]


# ------------------------------------------------------------------ A: threads in all parsers


def test_copilot_parser_captures_recap_threads(tmp_path: Path):
    from devlog.sources.copilot import CopilotParser

    root = tmp_path / "copilot"
    shutil.copytree(SAMPLES / "copilot", root)
    events = next(root.rglob("events.jsonl"))
    with events.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant.message", "timestamp": "2026-07-20T14:30:00Z",
                             "data": {"content": RECAP}}) + "\n")
    [session] = CopilotParser().iter_sessions(root)
    assert _threads(session) == [["add fixtures", "wire into config"]]


def test_grok_parser_captures_recap_threads(tmp_path: Path):
    from devlog.sources.grok import GrokParser

    root = tmp_path / "grok"
    shutil.copytree(SAMPLES / "grok", root)
    history = next(root.rglob("chat_history.jsonl"))
    with history.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant", "content": RECAP}) + "\n")
    [session] = GrokParser().iter_sessions(root)
    assert _threads(session) == [["add fixtures", "wire into config"]]


def test_opencode_parser_reads_full_assistant_text_for_threads(tmp_path: Path):
    from devlog.sources.opencode import OpenCodeParser

    db = tmp_path / "opencode.db"
    shutil.copy(SAMPLES / "opencode" / "opencode.db", db)
    con = sqlite3.connect(db)
    msg = con.execute("SELECT id, session_id, time_created FROM message "
                      "WHERE data LIKE '%assistant%' LIMIT 1").fetchone()
    assert msg is not None, "sample db should contain an assistant message"
    long_recap = "x " * 400 + RECAP  # follow-ups after the 400-char truncation point
    con.execute("INSERT INTO part (id, message_id, session_id, time_created, data) "
                "VALUES (?,?,?,?,?)",
                ("part_recap", msg[0], msg[1], msg[2],
                 json.dumps({"type": "text", "text": long_recap})))
    con.commit()
    con.close()
    [session] = OpenCodeParser().iter_sessions(tmp_path)
    assert _threads(session) == [["add fixtures", "wire into config"]]
    # Assistant text never becomes a user prompt.
    assert all("add fixtures" not in (e.user_message or "") for e in session.events)


_ = datetime, UTC  # used by later sections


# ------------------------------------------------------------------ B + C: identity, richer posts


def _publish_cfg(tmp_path: Path, **kw):
    from devlog.config import DevlogConfig

    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    return DevlogConfig(sources=["codex"], codex_root=str(SAMPLES / "codex"),
                        repo_path=str(repo).replace("\\", "/"), publish_mode="manual", **kw)


def test_public_post_uses_project_aliases(tmp_path: Path):
    from datetime import date

    from devlog.publish import publish_day

    cfg = _publish_cfg(tmp_path, project_aliases={"gurukul": "dharma-engine"})
    out = publish_day(cfg, date(2026, 7, 20), dry_run=True)
    assert "dharma-engine" in out["post"] and "gurukul" not in out["post"].lower()


def test_projects_post_has_shipped_and_stack_instead_of_tool_counts():
    from datetime import timedelta

    from devlog.models import SessionDigest
    from devlog.summarize import generate_post

    t0 = datetime(2026, 8, 13, 9, tzinfo=UTC)
    s = SessionDigest(session_id="a", project_path="/w/atlas", source="codex", start_time=t0,
                      end_time=t0 + timedelta(minutes=30), active_minutes=30.0,
                      user_messages=["run scanpy QC on the atlas"], tool_calls={"Read": 9},
                      files_touched={"/w/atlas/qc.py"})
    post = generate_post([s], public_detail="projects", commit_counts={"atlas": 3})
    assert "Shipped 3 commit(s)." in post
    assert "Stack: Python, scanpy" in post and "single-cell" not in post.split("Stack:")[0]
    assert "Read (9x)" not in post
    verbatim = generate_post([s], public_detail="verbatim", commit_counts={"atlas": 3})
    assert "Shipped 3 commit(s)." in verbatim and "Read (9x)" in verbatim
    summary = generate_post([s], public_detail="summary", commit_counts={"atlas": 3})
    assert "Shipped 3 commit(s)." in summary and "atlas" not in summary and "Stack" not in summary


def test_count_commits_counts_each_repo_once(tmp_path: Path):
    import os
    import subprocess
    from datetime import date

    from devlog.projects import ProjectResolver, count_commits

    repo = tmp_path / "vitreous"
    (repo / "src").mkdir(parents=True)
    noon = datetime(2026, 8, 13, 12).astimezone().isoformat()
    env = {**os.environ, "GIT_AUTHOR_DATE": noon, "GIT_COMMITTER_DATE": noon}
    for args in (["init", "-q"], ["config", "user.email", "me@x.com"],
                 ["config", "user.name", "Me"],
                 ["commit", "-q", "--allow-empty", "-m", "one"],
                 ["commit", "-q", "--allow-empty", "-m", "two"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)
    counts = count_commits([str(repo), str(repo / "src"), "/nowhere/x"],
                           ProjectResolver(), date(2026, 8, 13))
    assert counts == {"vitreous": 2}
