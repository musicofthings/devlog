from datetime import UTC, datetime, timedelta
from pathlib import Path

from devlog.digest import build_raw_digest
from devlog.models import SessionDigest
from devlog.privacy import redact_sensitive_text
from devlog.summarize import summarize_with_template


def _sensitive_session() -> SessionDigest:
    start = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
    return SessionDigest(
        session_id="secret",
        project_path="/projects/private",
        source="codex",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        user_messages=["Use API_TOKEN=top-secret-value for the request"],
        bash_commands=["curl -H 'Authorization: Bearer secret-bearer-token' example.test"],
    )


def test_redacts_common_secret_shapes():
    text = redact_sensitive_text(
        "ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnop "
        "Authorization: Bearer secret-bearer-token"
    )

    assert "abcdefghijklmnop" not in text
    assert "secret-bearer-token" not in text
    assert text.count("[REDACTED_SECRET]") == 2


def test_digest_and_template_redact_transcript_secrets():
    session = _sensitive_session()
    session.project_path = str(Path.home() / "projects" / "private")

    digest = build_raw_digest([session])
    post = summarize_with_template([session])

    assert "top-secret-value" not in digest
    assert "secret-bearer-token" not in digest
    assert str(Path.home()) not in digest
    assert "top-secret-value" not in post
