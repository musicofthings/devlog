from datetime import UTC, datetime

from devlog.models import RawSession, SessionDigest, SessionEvent


def test_session_digest_duration_minutes():
    start = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
    end = datetime(2026, 7, 22, 9, 30, tzinfo=UTC)
    d = SessionDigest(
        session_id="s1",
        project_path="/proj",
        source="claude_code",
        start_time=start,
        end_time=end,
    )
    assert d.duration_minutes == 30.0


def test_raw_session_holds_events():
    ts = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
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
