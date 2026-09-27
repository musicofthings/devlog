"""Ideas beyond the roadmap: local post writer, PR links in day notes, --explain."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from devlog.config import DevlogConfig, save_config
from devlog.models import SessionDigest
from devlog.privacy import configure_redaction
from devlog.summarize import generate_post
from tests.test_remaining import _day, _fake_ollama, _vault

SAMPLES = Path(__file__).resolve().parents[1] / "sample_data"
T0 = datetime(2026, 8, 13, 9, tzinfo=UTC)


def _session(msgs=("fix the scanpy loader",)) -> SessionDigest:
    return SessionDigest(session_id="a", project_path="/w/atlas", source="codex",
                         start_time=T0, end_time=T0 + timedelta(minutes=30),
                         active_minutes=30.0, user_messages=list(msgs),
                         tool_calls={"Edit": 2}, files_touched={"/w/atlas/load.py"})


@pytest.fixture(autouse=True)
def _reset_redaction():
    yield
    configure_redaction([])


# ------------------------------------------------------------------ 1: local post writer


class _FakeClient:
    def __init__(self, reply: str | Exception):
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt: str, model: str) -> str:
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_local_writer_uses_the_privacy_reduced_digest_and_cleans_output():
    client = _FakeClient("Here's the post:\nI fixed the loader on atlas! 🚀 It works now. "
                         "Tests pass. Shipped. Extra sentence. One more.")
    report: dict = {}
    post = generate_post([_session()], public_detail="projects", post_writer="ollama",
                         local_client=client, local_model="llama3.2", report=report)
    assert report["writer"] == "ollama:llama3.2"
    assert "Here's the post" not in post and "🚀" not in post and "!" not in post
    assert post.count(".") <= 5  # clamped like every other writer
    prompt = client.prompts[0]
    assert "atlas" in prompt and "Work: bugfix" in prompt
    assert "scanpy loader" not in prompt and "load.py" not in prompt  # projects level


def test_local_writer_output_is_redacted_and_falls_back_on_failure():
    configure_redaction([r"MRN\d{6}"])
    client = _FakeClient("I reviewed MRN123456 on atlas.")
    post = generate_post([_session()], public_detail="verbatim", post_writer="ollama",
                         local_client=client, local_model="m")
    assert "MRN123456" not in post and "[REDACTED]" in post

    report: dict = {}
    post = generate_post([_session()], public_detail="projects", post_writer="ollama",
                         local_client=_FakeClient(ConnectionError("down")), local_model="m",
                         report=report)
    assert report["writer"] == "template" and "local model failed" in report["reason"]
    assert post.startswith("Today I logged 30 active min across atlas.")


def test_writer_reasons_for_template_and_summary():
    report: dict = {}
    generate_post([_session()], public_detail="summary", post_writer="ollama",
                  local_client=_FakeClient("x"), local_model="m", report=report)
    assert report["writer"] == "template" and "summary" in report["reason"]
    report = {}
    generate_post([_session()], public_detail="projects", report=report)
    assert report["reason"] == "allow_external_api is off"


def test_local_writer_end_to_end_over_http(monkeypatch):
    from devlog.local_llm import OllamaClient

    server, url, requests = _fake_ollama()
    try:
        post = generate_post([_session()], public_detail="projects", post_writer="ollama",
                             local_client=OllamaClient(url), local_model="llama3.2")
        assert post == "I focused on the atlas. QC is still open."
        assert requests[0][0] == "/api/generate" and requests[0][1]["model"] == "llama3.2"
    finally:
        server.shutdown()


def test_config_validates_post_writer():
    with pytest.raises(ValueError, match="post_writer"):
        DevlogConfig(post_writer="gpt").validate()


# ------------------------------------------------------------------ 2: PRs in day notes


def test_pull_requests_for_a_day_via_gh():
    from devlog.projects import Project, ProjectResolver

    calls = []

    def fake_gh(cmd, cwd):
        calls.append(cmd)
        return CompletedProcess(cmd, 0, json.dumps([
            {"number": 7, "title": "Add UMAP", "url": "https://x/7", "state": "MERGED",
             "isDraft": False},
            {"number": 8, "title": "WIP", "url": "https://x/8", "state": "OPEN",
             "isDraft": True}]), "")

    resolver = ProjectResolver(gh_run=fake_gh)
    project = Project(name="atlas", repo_url="https://github.com/acme/atlas")
    prs = resolver.pull_requests(project, date(2026, 8, 13))
    assert [(p["number"], p["state"]) for p in prs] == [(7, "merged"), (8, "draft")]
    assert "updated:2026-08-13" in calls[0] and "@me" in calls[0]
    assert resolver.pull_requests(Project(name="local"), date(2026, 8, 13)) == []
    failing = ProjectResolver(gh_run=lambda c, w: CompletedProcess(c, 1, "", "auth"))
    assert failing.pull_requests(project, date(2026, 8, 13)) == []


def test_day_note_lists_pull_requests(tmp_path: Path):
    from devlog.obsidian import refresh_vault

    day = _day("2026-08-13", "atlas", ["add umap"])
    day["projects"][0]["pull_requests"] = [
        {"number": 7, "title": "Add UMAP #2", "url": "https://x/7", "state": "merged"}]
    cfg, root = _vault(tmp_path, {"2026-08-13": day})
    refresh_vault(cfg)
    note = (root / "2026-08-13.md").read_text(encoding="utf-8")
    assert "- **Pull requests:**" in note
    assert "    - [#7](https://x/7) Add UMAP \\#2 · merged" in note
    assert "pull_requests: 1" in note


# ------------------------------------------------------------------ 3: --explain


def test_explain_traces_sources_names_writer_and_redactions(tmp_path: Path, capsys):
    from devlog.publish import cmd_publish

    (tmp_path / "repo" / "docs").mkdir(parents=True)
    cfg_path = tmp_path / "c.toml"
    save_config(DevlogConfig(
        sources=["codex", "grok"], codex_root=str(SAMPLES / "codex"),
        grok_root=str(tmp_path / "no-grok"), repo_path=str(tmp_path / "repo"),
        project_aliases={"gurukul": "dharma-engine"}, redact_patterns=["(?i)dharma"],
        public_detail="verbatim",
    ), cfg_path)
    assert cmd_publish(["--dry-run", "--explain", "--date", "2026-07-20",
                        "--config", str(cfg_path)]) == 0
    out = capsys.readouterr().out
    assert "public_detail=verbatim post_writer=auto" in out
    assert "codex        1 session(s) parsed" in out
    assert "grok         no data root" in out
    assert "dharma-engine: named by alias (folder), 1 session(s)" in out
    assert "writer: template (allow_external_api is off)" in out
    # The alias itself matches the redaction pattern, so the post's project name is redacted.
    assert "1 user pattern(s)" in out and "0 match(es)" not in out
    assert "[REDACTED]" in out.split("---\n")[-1]


def test_explain_when_nothing_was_generated(tmp_path: Path, capsys):
    from devlog.publish import cmd_publish

    posts = tmp_path / "repo" / "posts"
    posts.mkdir(parents=True)
    (posts / "2026-07-20.md").write_text("# x\n\nold\n", encoding="utf-8")
    cfg_path = tmp_path / "c.toml"
    save_config(DevlogConfig(repo_path=str(tmp_path / "repo")), cfg_path)
    assert cmd_publish(["--dry-run", "--explain", "--date", "2026-07-20",
                        "--config", str(cfg_path)]) == 0
    assert "--explain: nothing was generated (skipped)" in capsys.readouterr().out


def test_resolver_records_why_a_name_was_chosen(tmp_path: Path):
    from devlog.projects import ProjectResolver

    home = tmp_path / "home"
    home.mkdir()
    r = ProjectResolver({"window": "devlog", "/a/b": "pinned"}, home=home)
    assert r.resolve("/a/b").source == "alias (path)"
    assert r.resolve("/x/window").source == "alias (folder)"
    assert r.resolve(str(home)).source == "home folder"
    assert r.resolve("/nowhere/thing").source == "folder name"
