from datetime import datetime, timezone
from unittest.mock import patch

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
