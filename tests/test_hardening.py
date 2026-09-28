"""Phase 4 hardening: rollback safety, privacy levels, empty days, incremental scans."""

from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from devlog.config import DevlogConfig
from devlog.delete_cmd import delete_day
from devlog.digest import build_raw_digest
from devlog.gitutil import default_git as real_git
from devlog.gitutil import undo_local_commit
from devlog.models import SessionDigest
from devlog.privacy import configure_redaction, redact_sensitive_text, user_redaction_count
from devlog.summarize import generate_post, summarize_with_template


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "posts").mkdir(parents=True)
    (repo / "docs").mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "me@example.com")
    _git(repo, "config", "user.name", "Me")
    (repo / "posts" / "2026-07-20.md").write_text("# 2026-07-20\n\nBuilt it.\n", encoding="utf-8")
    (repo / "notes.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


# ------------------------------------------------------------------ rollback (#4)


def test_undo_local_commit_keeps_unrelated_uncommitted_work(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text("my unsaved edit\n", encoding="utf-8")
    (repo / "scratch.py").write_text("print('wip')\n", encoding="utf-8")
    (repo / "posts" / "2026-07-21.md").write_text("# new\n", encoding="utf-8")
    _git(repo, "add", "posts")
    _git(repo, "commit", "-q", "-m", "publish")

    assert undo_local_commit(repo, real_git, "publish") is None
    assert _git(repo, "log", "--format=%s").split() == ["init"]
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "my unsaved edit\n"
    assert (repo / "scratch.py").exists()


def test_undo_local_commit_refuses_rather_than_discarding_overlapping_edits(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text("committed by devlog\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "publish")
    (repo / "notes.txt").write_text("edited after the commit\n", encoding="utf-8")

    note = undo_local_commit(repo, real_git, "publish")
    assert note is not None and "git reset --keep HEAD~1" in note
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "edited after the commit\n"


def test_failed_delete_push_restores_post_and_spares_other_work(tmp_path: Path):
    repo = _repo(tmp_path)
    _git(repo, "remote", "add", "origin", str(tmp_path / "no-such-remote.git"))
    (repo / "notes.txt").write_text("my unsaved edit\n", encoding="utf-8")
    cfg = DevlogConfig(repo_path=str(repo).replace("\\", "/"), remote="origin", branch="main")

    try:
        delete_day(cfg, date(2026, 7, 20), git_run=real_git)
    except RuntimeError as exc:
        assert "local delete commit was reset" in str(exc)
    else:  # pragma: no cover - the remote does not exist
        raise AssertionError("push to a missing remote should fail")
    assert (repo / "posts" / "2026-07-20.md").exists()
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "my unsaved edit\n"
    assert _git(repo, "log", "--format=%s").split() == ["init"]


# ------------------------------------------------------------------ privacy (#3)

T0 = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
PROMPT = "review variants for patient MRN123456 in cohort ALPHA-7"


def _session(project: str, msgs: list[str]) -> SessionDigest:
    return SessionDigest(
        session_id=project, project_path=f"/w/{project}", source="codex",
        start_time=T0, end_time=T0 + timedelta(minutes=40), user_messages=msgs,
        tool_calls={"Read": 5, "Edit": 2}, files_touched={"/w/secret_cohort.vcf"},
        bash_commands=["bcftools view cohort.vcf"], active_minutes=40.0,
    )


@pytest.fixture(autouse=True)
def _reset_redaction():
    yield
    configure_redaction([])


def test_public_detail_levels():
    sessions = [_session("variantdb", [PROMPT]), _session("devlog", ["add obsidian hubs"])]
    verbatim = summarize_with_template(sessions, "verbatim")
    assert "MRN123456" in verbatim  # the old behaviour, still available on request

    projects = summarize_with_template(sessions, "projects")
    assert "MRN123456" not in projects and "cohort" not in projects
    assert "variantdb" in projects and "code-review" in projects and "devlog" in projects

    summary = summarize_with_template(sessions, "summary")
    assert "variantdb" not in summary and "devlog" not in summary
    assert "in 2 project(s)" in summary


def test_llm_digest_honours_public_detail():
    sessions = [_session("variantdb", [PROMPT])]
    full = build_raw_digest(sessions, compact=True)
    assert "MRN123456" in full and "cohort.vcf" in full
    reduced = build_raw_digest(sessions, compact=True, detail="projects")
    assert "MRN123456" not in reduced and "vcf" not in reduced and "bcftools" not in reduced
    assert "variantdb" in reduced and "Work: code-review" in reduced
    anon = build_raw_digest(sessions, compact=True, detail="summary")
    assert "variantdb" not in anon and "project 1" in anon


def test_generate_post_passes_detail_to_the_template_fallback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    post = generate_post([_session("variantdb", [PROMPT])], public_detail="projects")
    assert "MRN123456" not in post


def test_user_redact_patterns_apply_everywhere():
    configure_redaction([r"MRN\d{6}", r"ALPHA-\d+"])
    assert redact_sensitive_text(PROMPT) == (
        "review variants for patient [REDACTED] in cohort [REDACTED]")
    post = summarize_with_template([_session("variantdb", [PROMPT])], "verbatim")
    assert "MRN123456" not in post and "[REDACTED]" in post


def test_config_rejects_bad_privacy_settings():
    with pytest.raises(ValueError, match="public_detail"):
        DevlogConfig(public_detail="everything").validate()
    with pytest.raises(ValueError, match="invalid regex"):
        DevlogConfig(redact_patterns=["("]).validate()


@pytest.mark.parametrize("pattern", ["", "x*", "a?", "(MRN\\d+)?"])
def test_config_rejects_patterns_matching_empty_string(pattern):
    with pytest.raises(ValueError, match="matches the empty string"):
        DevlogConfig(redact_patterns=[pattern]).validate()


def test_zero_length_matches_never_insert_markers():
    # Lookarounds and \b pass validation but only ever match zero characters.
    configure_redaction(["", r"\b", r"(?=MRN)", r"MRN\d{6}"])
    assert redact_sensitive_text("patient MRN123456 ok") == "patient [REDACTED] ok"
    assert user_redaction_count() == 1


# ------------------------------------------------------------------ empty days (#7)


def test_empty_day_is_not_published_but_is_mirrored(tmp_path: Path):
    from devlog.publish import publish_day

    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = DevlogConfig(
        sources=["codex"], codex_root=str(tmp_path / "no-logs"),
        repo_path=str(repo).replace("\\", "/"), publish_mode="manual",
        obsidian_vault=str(vault).replace("\\", "/"),
    )
    out = publish_day(cfg, date(2026, 7, 20))
    assert out["status"] == "skipped_empty"
    assert not (repo / "posts" / "2026-07-20.md").exists()
    assert (vault / "DevLog" / "2026-07-20.md").is_file()

    cfg.publish_empty_days = True
    assert publish_day(cfg, date(2026, 7, 20))["status"] == "written"
    assert "No coding activity" in (repo / "posts" / "2026-07-20.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------ incremental scan (#5)


def test_since_skips_logs_not_touched_since_the_target_day(tmp_path: Path):
    import os
    import shutil

    from devlog.publish import scan_start
    from devlog.sources.codex import CodexParser

    sample = Path(__file__).resolve().parents[1] / "sample_data" / "codex"
    root = tmp_path / "codex"
    shutil.copytree(sample, root)
    files = sorted(root.rglob("rollout-*.jsonl"))
    assert files
    everything = CodexParser().iter_sessions(root)
    assert everything

    since = scan_start(date(2026, 9, 1))
    old = since.timestamp() - 86_400
    for f in files:
        os.utime(f, (old, old))
    assert CodexParser().iter_sessions(root, since=since) == []
    assert len(CodexParser().iter_sessions(root)) == len(everything)  # rescan: no cutoff

    os.utime(files[0], None)  # touched now -> scanned again
    assert len(CodexParser().iter_sessions(root, since=since)) == 1


# ------------------------------------------------------------------ site templates (#12)


def test_templates_fill_in_one_pass_and_ship_as_files():
    from devlog import site

    page = site._page("t", "<p>@@TITLE@@ is literal user text</p>")
    assert "<p>@@TITLE@@ is literal user text</p>" in page
    assert "<title>t · Daily Dev Log</title>" in page
    assert "@@" not in site._admin_panel_html("o/r", "main")
    assert "{{" not in site._template("admin_panel.html")


def test_every_public_detail_post_round_trips_through_vault_backfill():
    from devlog.knowledge import parse_post_meta

    sessions = [_session("variantdb", [PROMPT]), _session("devlog", ["add obsidian hubs"])]
    day = date(2026, 8, 13)

    def meta(detail: str) -> dict:
        return parse_post_meta(day, f"# {day}\n\n{summarize_with_template(sessions, detail)}\n")

    verbatim = meta("verbatim")
    assert [p["slug"] for p in verbatim["projects"]] == ["devlog", "variantdb"]
    projects = meta("projects")
    assert [p["slug"] for p in projects["projects"]] == ["devlog", "variantdb"]
    assert {p["slug"]: p["work_types"] for p in projects["projects"]}["variantdb"] == [
        "code-review", "data-analysis"]
    summary = meta("summary")
    assert summary["projects"] == [] and summary["active_minutes"] == 80
    assert "code-review" in summary["work_types"]


def test_summary_detail_never_calls_the_llm(monkeypatch):
    from unittest.mock import patch

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch("devlog.summarize.summarize_with_claude") as mocked:
        post = generate_post([_session("variantdb", [PROMPT])], allow_external_api=True,
                             public_detail="summary")
    mocked.assert_not_called()
    assert post.startswith("Today I logged 40 active min in 1 project(s).")


# ------------------------------------------------------------------ init keeps hand edits


def test_rerunning_init_keeps_hand_edited_settings(tmp_path: Path, monkeypatch):
    from devlog.config import load_config, save_config
    from devlog.init_cmd import cmd_init

    monkeypatch.setattr("devlog.init_cmd.unregister_windows_task", lambda: None)
    monkeypatch.setattr("devlog.init_cmd.write_publish_now_shortcut",
                        lambda cfg, **kwargs: tmp_path / "Publish Devlog Now.cmd")
    monkeypatch.setattr("devlog.obsidian.obsidian_app_config_path",
                        lambda: tmp_path / "obsidian.json")
    monkeypatch.setattr("devlog.obsidian.default_new_vault_path", lambda: tmp_path / "Vault")

    cfg_path = tmp_path / "devlog" / "config.toml"
    save_config(DevlogConfig(
        project_aliases={"window": "devlog"}, topics={"CRISPR screens": ["crispr"]},
        redact_patterns=[r"MRN\d{6}"], publish_empty_days=True, publish_mode="auto",
    ), cfg_path)

    assert cmd_init(["--defaults", "--no-schedule", "--config", str(cfg_path)]) == 0
    loaded = load_config(cfg_path)
    assert loaded.project_aliases == {"window": "devlog"}
    assert loaded.topics == {"CRISPR screens": ["crispr"]}
    assert loaded.redact_patterns == [r"MRN\d{6}"]
    assert loaded.publish_empty_days is True
    assert loaded.publish_mode == "manual"  # prompted settings still reset to the answer
