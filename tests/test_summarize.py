from datetime import UTC, datetime
from unittest.mock import patch

from devlog.models import SessionDigest
from devlog.summarize import generate_post, summarize_with_template


def _sess() -> SessionDigest:
    return SessionDigest(
        session_id="s",
        project_path="/Users/dev/code/variantgpt",
        source="claude_code",
        start_time=datetime(2026, 7, 22, 9, 0, tzinfo=UTC),
        end_time=datetime(2026, 7, 22, 9, 20, tzinfo=UTC),
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


def test_external_api_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch("devlog.summarize.summarize_with_claude") as mocked:
        text = generate_post([_sess()])
    mocked.assert_not_called()
    assert "variantgpt" in text


def test_generate_post_uses_claude_when_key_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake = (
        "I spent time on variantgpt refactoring the VCF parser to stream. "
        "Edited the parser module and ran pytest. "
        "Also sketched a progress bar for large files."
    )
    with patch("devlog.summarize.summarize_with_claude", return_value=fake) as mocked:
        text = generate_post([_sess()], allow_external_api=True)
    mocked.assert_called_once()
    assert text == fake
    assert len(text.split()) <= 120
    assert 3 <= text.count(".") <= 5 or 3 <= len([s for s in text.split(".") if s.strip()]) <= 5


def test_generate_post_redacts_claude_api_output(monkeypatch):
    """API path must run a final redact even if the model echoes a secret."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    leaked = (
        "I used Authorization: Bearer secret-bearer-token while testing the API. "
        "Then I fixed the parser. Also ran pytest."
    )
    with patch("devlog.summarize.summarize_with_claude", return_value=leaked):
        text = generate_post([_sess()], allow_external_api=True)
    assert "secret-bearer-token" not in text
    assert "[REDACTED_SECRET]" in text


def test_claude_failure_falls_back(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch("devlog.summarize.summarize_with_claude", side_effect=RuntimeError("boom")):
        text = generate_post([_sess()], allow_external_api=True)
    assert "variantgpt" in text


def test_empty_day_skips_claude_api(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch("devlog.summarize.summarize_with_claude") as mocked:
        text = generate_post([], allow_external_api=True)
    mocked.assert_not_called()
    assert text == "No coding activity logged today."


def test_generate_post_sends_compact_digest(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch("devlog.summarize.summarize_with_claude", return_value="ok.") as mocked:
        generate_post([_sess()], allow_external_api=True)
    digest_arg = mocked.call_args.args[0]
    assert "source=" not in digest_arg
    assert "variantgpt" in digest_arg
    assert "Total active time:" not in digest_arg


def test_clamp_sentences_caps_long_posts():
    from devlog.summarize import _clamp_sentences

    long = "One. Two. Three. Four. Five. Six. Seven."
    assert _clamp_sentences(long, max_sentences=5) == "One. Two. Three. Four. Five."


def test_clamp_sentences_keeps_decimals_intact():
    from devlog.summarize import _clamp_sentences

    text = "Spent 1.5 hours on the v2.3 parser. Ran pytest."
    assert _clamp_sentences(text, max_sentences=2) == text
    assert _clamp_sentences("Spent 1.5 hours. Two. Three.", max_sentences=1) == "Spent 1.5 hours."


def test_template_handles_windows_paths():
    sess = _sess()
    sess.project_path = r"C:\Users\dev\code\variantgpt"
    text = summarize_with_template([sess])
    # Project name, not the whole backslashed path
    assert "variantgpt: Refactor" in text
    assert r"C:\Users" not in text.replace("variantgpt", "")


def test_template_is_bounded_for_many_sessions():
    sessions = []
    for index in range(10):
        session = _sess()
        session.session_id = str(index)
        session.project_path = f"/projects/p{index}"
        session.user_messages = [f"Implement task {index}. This second sentence should be removed."]
        sessions.append(session)

    text = summarize_with_template(sessions)

    assert len([part for part in text.split(".") if part.strip()]) == 3
    assert len(text.split()) <= 120
