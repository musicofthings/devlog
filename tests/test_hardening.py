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
from devlog.privacy import configure_redaction, redact_sensitive_text
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
    assert "across 2 project(s)" in summary


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
