from datetime import datetime, timezone
from devlog.models import SessionEvent, RawSession, SessionDigest

def test_session_digest_duration_minutes():
    start = datetime(2026, 7, 22, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 22, 9, 30, tzinfo=timezone.utc)
    d = SessionDigest(
        session_id="s1",
        project_path="/proj",
        source="claude_code",
        start_time=start,
        end_time=end,
    )
    assert d.duration_minutes == 30.0

def test_raw_session_holds_events():
    ts = datetime(2026, 7, 22, 9, 0, tzinfo=timezone.utc)
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
