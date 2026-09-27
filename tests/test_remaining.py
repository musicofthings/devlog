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


# ------------------------------------------------------------------ D-H: vault extras

def _day(d: str, slug: str, tasks: list[str], *, minutes: int = 30, tbm=None, root=None,
         repo=None, threads=()) -> dict:
    return {
        "date": d, "active_minutes": minutes, "sessions": 1, "sources": ["claude_code"],
        "work_types": ["feature"], "summary": f"Worked on {d}.", "origin": "sessions",
        "projects": [{
            "slug": slug, "name": slug, "minutes": minutes, "sessions": 1,
            "sources": ["claude_code"], "work_types": ["feature"], "tasks": tasks,
            "files": [], "tools": {}, "threads": list(threads), "commits": [],
            "tokens": {"in": 0, "out": 0, "cache": 0}, "tokens_by_model": tbm or {},
            "root": root, "repo_url": repo,
        }],
    }


def _vault(tmp_path: Path, days: dict, **cfg):
    from devlog.config import DevlogConfig
    from devlog.vault_graph import save_index

    root = tmp_path / "vault" / "DevLog"
    root.mkdir(parents=True)
    for d in days:
        (root / f"{d}.md").write_text("", encoding="utf-8")
    save_index(root, days)
    return DevlogConfig(obsidian_vault=str(tmp_path / "vault").replace("\\", "/"), **cfg), root


def test_quarterly_rollups_and_links(tmp_path: Path):
    from devlog.obsidian import refresh_vault

    days = {"2026-08-13": _day("2026-08-13", "atlas", ["add qc"]),
            "2026-10-02": _day("2026-10-02", "atlas", ["add umap"])}
    cfg, root = _vault(tmp_path, days)
    refresh_vault(cfg)
    q3 = (root / "Quarterly" / "2026-Q3.md").read_text(encoding="utf-8")
    assert "type: devlog-quarter" in q3 and "start: 2026-07-01" in q3 and "end: 2026-09-30" in q3
    assert "[[DevLog/Monthly/2026-08|2026-08]]" in q3 and "# 2026 Q3" in q3
    assert "[[DevLog/Quarterly/2026-Q4|2026-Q4]] →" in q3
    month = (root / "Monthly" / "2026-08.md").read_text(encoding="utf-8")
    assert "[[DevLog/Quarterly/2026-Q3|2026-Q3]]" in month
    assert "## Quarters" in (root / "DevLog Home.md").read_text(encoding="utf-8")


def test_readme_summary_skips_badges_headings_and_code(tmp_path: Path):
    from devlog.projects import readme_summary

    (tmp_path / "README.md").write_text(
        "# Atlas\n\n[![CI](x)](y)\n\n```bash\npip install atlas\n```\n\n"
        "Single-cell atlas tooling\nfor QC and clustering.\n\nMore text.\n", encoding="utf-8")
    assert readme_summary(tmp_path) == "Single-cell atlas tooling for QC and clustering."
    assert readme_summary(tmp_path / "missing") is None


def test_open_pull_requests_parses_gh_output():
    from subprocess import CompletedProcess

    from devlog.projects import open_pull_requests

    calls = []

    def fake_gh(cmd, cwd):
        calls.append(cmd)
        return CompletedProcess(cmd, 0, json.dumps(
            [{"number": 7, "title": "Add UMAP", "url": "https://x/7", "isDraft": True}]), "")

    prs = open_pull_requests("https://github.com/acme/atlas", runner=fake_gh)
    assert prs == [{"number": 7, "title": "Add UMAP", "url": "https://x/7", "draft": True}]
    assert calls[0][:5] == ["gh", "pr", "list", "--repo", "acme/atlas"]
    failing = open_pull_requests(
        "https://github.com/acme/atlas",
        runner=lambda c, w: CompletedProcess(c, 1, "", "not logged in"))
    assert failing == []


def test_project_hub_shows_readme_and_prs(tmp_path: Path):
    from devlog.vault_graph import load_index, refresh_graph

    days = {"2026-08-13": _day("2026-08-13", "atlas", ["add qc"], root="/r",
                               repo="https://github.com/acme/atlas")}
    _, root = _vault(tmp_path, days)
    seen = []

    def info(r, url):
        seen.append((r, url))
        return {"readme": "Atlas #1 tooling", "pull_requests": [
            {"number": 7, "title": "Add UMAP", "url": "https://x/7", "draft": False}]}

    refresh_graph(tmp_path / "vault", "DevLog", load_index(root), project_info=info)
    hub = (root / "Projects" / "atlas.md").read_text(encoding="utf-8")
    assert seen == [("/r", "https://github.com/acme/atlas")]
    assert "## About" in hub and "> Atlas \\#1 tooling" in hub
    assert "- [#7](https://x/7) Add UMAP" in hub


def test_cost_estimates_use_prices_and_flag_unpriced_models(tmp_path: Path):
    from devlog.obsidian import refresh_vault
    from devlog.pricing import estimate, normalize_model, price_table

    assert normalize_model("claude-opus-4-8-20260101[1m]") == "claude-opus-4-8"
    usd, unpriced = estimate({"claude-sonnet-5": {"in": 1_000_000, "out": 100_000,
                                                  "cache": 2_000_000}}, price_table())
    assert round(usd, 2) == 2.0 + 1.0 + 0.4 and unpriced == 0
    assert estimate({"gpt-5-codex": {"in": 10, "out": 0, "cache": 0}}, price_table())[1] == 10

    tbm = {"claude-sonnet-5": {"in": 1_000_000, "out": 0, "cache": 0},
           "gpt-5-codex": {"in": 500_000, "out": 0, "cache": 0}}
    days = {"2026-08-13": _day("2026-08-13", "atlas", ["add qc"], tbm=tbm)}
    days["2026-08-13"]["projects"][0]["tokens"] = {"in": 1_500_000, "out": 0, "cache": 0}
    days["2026-08-13"]["tokens"] = {"in": 1_500_000, "out": 0, "cache": 0}
    cfg, root = _vault(tmp_path, days)
    refresh_vault(cfg)
    note = (root / "2026-08-13.md").read_text(encoding="utf-8")
    assert "cost_usd: 2.00" in note and "≈ $2.00 + unpriced models" in note

    cfg.model_prices = {"gpt-5-codex": [1.0, 8.0, 0.1]}
    refresh_vault(cfg)
    note = (root / "2026-08-13.md").read_text(encoding="utf-8")
    assert "cost_usd: 2.50" in note and "unpriced" not in note
    assert "API-equivalent cost ≈ $2.50" in (root / "DevLog Home.md").read_text(encoding="utf-8")


def test_parsers_record_model_for_tokens(tmp_path: Path):
    from datetime import date

    from devlog.digest import slice_for_date
    from devlog.sources.claude_code import ClaudeCodeParser
    from devlog.sources.codex import parse_rollout_file

    sessions = ClaudeCodeParser().iter_sessions(SAMPLES / "claude_code")
    digests = slice_for_date(sessions, date(2026, 7, 22), UTC)
    assert digests and all(set(d.tokens_by_model) == {"claude-opus-4-8"} for d in digests)
    variantgpt = next(d for d in digests if d.project_path.endswith("variantgpt"))
    assert variantgpt.tokens_by_model["claude-opus-4-8"] == {"in": 1480, "out": 760,
                                                              "cache": 51000}

    rollout = tmp_path / "rollout-x.jsonl"
    lines = [
        {"timestamp": "2026-08-13T09:00:00Z", "type": "turn_context",
         "payload": {"model": "gpt-5-codex"}},
        {"timestamp": "2026-08-13T09:00:01Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "fix it"}},
        {"timestamp": "2026-08-13T09:00:05Z", "type": "event_msg",
         "payload": {"type": "token_count", "info": {"last_token_usage": {
             "input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 50}}}},
    ]
    rollout.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    [d] = slice_for_date([parse_rollout_file(rollout)], date(2026, 8, 13), UTC)
    assert d.tokens_by_model == {"gpt-5-codex": {"in": 100, "out": 20, "cache": 50}}


# ------------------------------------------------------------------ G + H: local models

def _fake_ollama():
    """A real HTTP server speaking the two Ollama endpoints devlog uses."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    requests: list[tuple[str, dict]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep test output quiet
            pass

        def do_POST(self):  # noqa: N802 - http.server API
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            if self.path == "/api/embed":
                # 2-d "embeddings": scanpy-ish text points one way, everything else another.
                out = {"embeddings": [[1.0, 0.0] if "scanpy" in t or "leiden" in t
                                      else [0.0, 1.0] for t in body["input"]]}
            else:
                out = {"response": "I focused on the atlas.\nQC is still open."}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}", requests


def test_ollama_embeddings_drive_related_days_and_are_cached(tmp_path: Path):
    from devlog.obsidian import refresh_vault

    server, url, requests = _fake_ollama()
    try:
        days = {"2026-08-10": _day("2026-08-10", "atlas", ["scanpy qc"]),
                "2026-08-11": _day("2026-08-11", "cells", ["leiden clustering"]),
                "2026-08-12": _day("2026-08-12", "web", ["css layout"])}
        cfg, root = _vault(tmp_path, days, related_backend="ollama", ollama_url=url)
        refresh_vault(cfg)
        note = (root / "2026-08-10.md").read_text(encoding="utf-8")
        # Semantic link across projects that share no words.
        assert "## Related days" in note and "[[DevLog/2026-08-11|2026-08-11]]" in note
        assert "2026-08-12" not in note.split("## Related days")[1]
        assert [p for p, _ in requests] == ["/api/embed"]
        refresh_vault(cfg)
        assert len(requests) == 1  # cached: unchanged days are not re-embedded
    finally:
        server.shutdown()


def test_ollama_down_falls_back_to_tfidf(tmp_path: Path):
    from devlog.obsidian import refresh_vault

    days = {"2026-08-10": _day("2026-08-10", "atlas", ["scanpy qc atlas"]),
            "2026-08-11": _day("2026-08-11", "atlas", ["scanpy leiden atlas"])}
    cfg, root = _vault(tmp_path, days, related_backend="ollama",
                       ollama_url="http://127.0.0.1:9", period_retros=True)
    assert refresh_vault(cfg)["status"] == "refreshed"
    note = (root / "2026-08-10.md").read_text(encoding="utf-8")
    assert "[[DevLog/2026-08-11|2026-08-11]] · [[DevLog/Projects/atlas|atlas]] — shared:" in note
    assert "Retro" not in (root / "Weekly" / "2026-W33.md").read_text(encoding="utf-8")


def test_period_retro_from_local_model_is_cached(tmp_path: Path):
    from devlog.obsidian import refresh_vault

    server, url, requests = _fake_ollama()
    try:
        days = {"2026-08-10": _day("2026-08-10", "atlas", ["scanpy qc"])}
        cfg, root = _vault(tmp_path, days, period_retros=True, ollama_url=url)
        refresh_vault(cfg)
        week = (root / "Weekly" / "2026-W33.md").read_text(encoding="utf-8")
        assert "> [!quote] Retro (written by a local model)" in week
        assert "> I focused on the atlas." in week and "> QC is still open." in week
        prompt = next(body["prompt"] for path, body in requests if path == "/api/generate")
        assert "2026-08-10: scanpy qc" in prompt and "[[" not in prompt
        n = len(requests)
        refresh_vault(cfg)
        assert len(requests) == n  # facts unchanged -> no new generation
    finally:
        server.shutdown()


def test_dry_run_on_an_empty_day_says_why(tmp_path: Path, capsys):
    from devlog.config import DevlogConfig, save_config
    from devlog.publish import cmd_publish

    cfg_path = tmp_path / "c.toml"
    save_config(DevlogConfig(sources=["codex"], codex_root=str(tmp_path / "none"),
                             repo_path=str(tmp_path).replace("\\", "/")), cfg_path)
    assert cmd_publish(["--dry-run", "--date", "2026-07-20", "--config", str(cfg_path)]) == 0
    out = capsys.readouterr().out
    assert "skipped_empty: 2026-07-20" in out and "publish_empty_days = false" in out
