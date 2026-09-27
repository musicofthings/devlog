"""public_detail and redact_patterns: what reaches the public post vs. the vault."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from devlog.config import DevlogConfig, load_config, save_config
from devlog.digest import build_raw_digest
from devlog.knowledge import parse_post_meta
from devlog.models import SessionDigest
from devlog.privacy import redact_sensitive_text
from devlog.summarize import build_projects_digest, generate_post, summarize_with_template
from devlog.vault_graph import load_index

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
PROMPT = "Fix the variant filter for sample S-12345 from patient MRN 00123456"
MRN = r"\bMRN\s*\d{6,10}\b"
SAMPLE = r"\bS-\d{5}\b"


def _sess(project: str = "variantgpt", messages: list[str] | None = None) -> SessionDigest:
    return SessionDigest(
        session_id=project,
        project_path=f"/work/{project}",
        source="claude_code",
        start_time=T0,
        end_time=T0 + timedelta(minutes=40),
        user_messages=[PROMPT] if messages is None else messages,
        tool_calls={"Edit": 3, "Bash": 1},
        files_touched={f"/work/{project}/filters.py"},
        bash_commands=["bcftools view data/S-12345.vcf.gz"],
        active_minutes=40.0,
    )


# ------------------------------------------------------------------ config


def test_defaults_are_projects_and_no_patterns():
    cfg = DevlogConfig()
    assert cfg.public_detail == "projects"
    assert cfg.redact_patterns == []


def test_save_and_load_round_trips_both_settings(tmp_path: Path):
    path = tmp_path / "config.toml"
    cfg = DevlogConfig(public_detail="summary", redact_patterns=[MRN, SAMPLE, r'"quoted"'])
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded is not None
    assert loaded.public_detail == "summary"
    assert loaded.redact_patterns == [MRN, SAMPLE, r'"quoted"']


def test_load_config_without_keys_uses_defaults(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('publish_mode = "manual"\n', encoding="utf-8")
    loaded = load_config(path)
    assert loaded is not None
    assert loaded.public_detail == "projects"
    assert loaded.redact_patterns == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"public_detail": "everything"}, "public_detail"),
        ({"redact_patterns": ["(unclosed"]}, "invalid regex"),
        ({"redact_patterns": [""]}, "redact_patterns"),
        ({"redact_patterns": "MRN\\d+"}, "redact_patterns"),
    ],
)
def test_validate_rejects_bad_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        DevlogConfig(**kwargs).validate()


def test_load_config_rejects_invalid_regex(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("redact_patterns = ['[a-']\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid regex"):
        load_config(path)


# ------------------------------------------------------------------ redaction


def test_redact_sensitive_text_applies_user_patterns():
    text = redact_sensitive_text(PROMPT, [MRN, SAMPLE])
    assert "00123456" not in text
    assert "S-12345" not in text
    assert text.count("[REDACTED]") == 2
    # Built-in secret redaction still runs alongside.
    assert "[REDACTED_SECRET]" in redact_sensitive_text("TOKEN=abc MRN 1234567", [MRN])


def test_raw_digest_applies_user_patterns():
    digest = build_raw_digest([_sess()], compact=True, redact_patterns=[MRN, SAMPLE])
    assert "00123456" not in digest
    assert "S-12345" not in digest


def test_patterns_redact_project_names_too():
    post = summarize_with_template([_sess("cohort-S-54321")], "summary", [SAMPLE])
    assert "S-54321" not in post
    post = summarize_with_template([_sess("cohort-S-54321")], "projects", [SAMPLE])
    assert "S-54321" not in post
    assert "cohort-[REDACTED]" in post


# ------------------------------------------------------------------ template


def test_summary_detail_is_minutes_and_project_count_only():
    post = summarize_with_template([_sess(), _sess("devlog", ["write the README"])], "summary")
    assert post == (
        "Today I logged 80 active min in 2 project(s). I recorded activity in 2 coding session(s)."
    )


def test_projects_detail_has_names_and_work_types_but_no_prompt_text():
    post = summarize_with_template([_sess(), _sess("devlog", ["write the README"])], "projects")
    assert "variantgpt (bugfix" in post
    assert "devlog (docs)" in post
    assert "variant filter" not in post
    assert "MRN" not in post
    assert "README" not in post
    assert "filters.py" not in post


def test_projects_detail_without_work_types_lists_bare_names():
    post = summarize_with_template([_sess("devlog", ["hmm"])], "projects")
    assert "I worked on devlog (feature)." in post  # tool-mix fallback: Edit
    bare = _sess("devlog", ["hmm"])
    bare.tool_calls = {}
    assert "I worked on devlog." in summarize_with_template([bare], "projects")


def test_verbatim_detail_keeps_prompt_but_applies_patterns():
    post = summarize_with_template([_sess()], "verbatim", [MRN, SAMPLE])
    assert "variantgpt: Fix the variant filter" in post
    assert "00123456" not in post
    assert "S-12345" not in post


def test_template_rejects_unknown_detail():
    with pytest.raises(ValueError, match="public_detail"):
        summarize_with_template([_sess()], "everything")


def test_projects_post_still_parses_for_vault_backfill():
    post = summarize_with_template([_sess(), _sess("devlog", ["write the README"])], "projects")
    meta = parse_post_meta(date(2026, 9, 1), f"# 2026-09-01\n\n{post}\n")
    assert [p["slug"] for p in meta["projects"]] == ["devlog", "variantgpt"]
    assert all(p["tasks"] == [] for p in meta["projects"])


def test_summary_post_is_not_misparsed_as_a_project():
    post = summarize_with_template([_sess()], "summary")
    meta = parse_post_meta(date(2026, 9, 1), f"# 2026-09-01\n\n{post}\n")
    assert meta["projects"] == []


# ------------------------------------------------------------------ LLM path


def test_llm_projects_digest_has_no_prompt_text(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch("devlog.summarize.summarize_with_claude", return_value="ok.") as mocked:
        generate_post([_sess()], allow_external_api=True, public_detail="projects")
    digest = mocked.call_args.args[0]
    assert "variantgpt" in digest
    assert "Work: bugfix" in digest
    assert "variant filter" not in digest
    assert "filters.py" not in digest
    assert "bcftools" not in digest
    assert digest == build_projects_digest([_sess()])


def test_llm_verbatim_digest_applies_patterns(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch("devlog.summarize.summarize_with_claude", return_value="ok.") as mocked:
        generate_post(
            [_sess()],
            allow_external_api=True,
            public_detail="verbatim",
            redact_patterns=[MRN, SAMPLE],
        )
    digest = mocked.call_args.args[0]
    assert "variant filter" in digest
    assert "00123456" not in digest
    assert "S-12345" not in digest


def test_llm_output_is_redacted_with_patterns(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    echoed = "I fixed filtering for MRN 00123456. Ran tests. Shipped it."
    with patch("devlog.summarize.summarize_with_claude", return_value=echoed):
        post = generate_post(
            [_sess()], allow_external_api=True, public_detail="verbatim", redact_patterns=[MRN]
        )
    assert "00123456" not in post
    assert "[REDACTED]" in post


def test_llm_skipped_for_summary_detail(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch("devlog.summarize.summarize_with_claude") as mocked:
        post = generate_post([_sess()], allow_external_api=True, public_detail="summary")
    mocked.assert_not_called()
    assert post.startswith("Today I logged 40 active min in 1 project(s).")


def test_generate_post_defaults_to_projects(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    post = generate_post([_sess()])
    assert "variantgpt (" in post
    assert "variant filter" not in post


# ------------------------------------------------------------------ vault keeps detail


def _publish_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "docs" / "index.html").write_text(
        '<a href="https://github.com/musicofthings/devlog">Open on GitHub →</a>\n',
        encoding="utf-8",
    )
    return repo


@pytest.mark.parametrize("detail", ["summary", "projects", "verbatim"])
def test_vault_keeps_full_detail_for_every_public_detail(tmp_path: Path, detail: str):
    from devlog.obsidian import archive_path
    from devlog.publish import publish_day

    repo = _publish_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "codex"
    cfg = DevlogConfig(
        sources=["codex"],
        codex_root=str(sample),
        repo_path=str(repo).replace("\\", "/"),
        publish_mode="manual",
        obsidian_vault=str(vault).replace("\\", "/"),
        public_detail=detail,
    )
    day = date(2026, 7, 20)
    out = publish_day(cfg, day, force=True)
    assert out["obsidian"]["status"] == "written"

    prompt = "Implement the Gurukul multi-agent blueprint"
    public = (repo / "posts" / "2026-07-20.md").read_text(encoding="utf-8")
    assert (prompt in public) == (detail == "verbatim")
    if detail == "summary":
        assert "gurukul" not in public.lower()

    meta = load_index(vault / "DevLog")["2026-07-20"]
    assert meta["origin"] == "sessions"
    [project] = meta["projects"]
    assert project["slug"] == "gurukul"
    assert project["tasks"] == [prompt]
    assert "feature" in project["work_types"]
    note = archive_path(cfg, day).read_text(encoding="utf-8")
    assert prompt in note
