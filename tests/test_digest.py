from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from devlog.digest import build_raw_digest, slice_for_date, total_active_minutes
from devlog.models import RawSession, SessionEvent

IST = ZoneInfo("Asia/Kolkata")


def _ev(ts: datetime, **kwargs) -> SessionEvent:
    return SessionEvent(timestamp=ts, **kwargs)


def test_midnight_spanning_session_splits_without_double_count():
    # Session 23:30 IST day1 -> 00:30 IST day2
    start = datetime(2026, 7, 22, 23, 30, tzinfo=IST)
    mid = datetime(2026, 7, 23, 0, 10, tzinfo=IST)
    end = datetime(2026, 7, 23, 0, 30, tzinfo=IST)
    raw = RawSession(
        session_id="span1",
        project_path="/proj",
        source="claude_code",
        start_time=start,
        end_time=end,
        events=[
            _ev(start, user_message="before midnight"),
            _ev(
                start + timedelta(minutes=5),
                tool_name="Edit",
                file_path="/proj/a.py",
                tokens_in=100,
                tokens_out=50,
            ),
            _ev(mid, user_message="after midnight"),
            _ev(end, tool_name="Bash", bash_command="pytest", tokens_in=40, tokens_out=20),
        ],
    )
    day1 = slice_for_date([raw], date(2026, 7, 22), IST)
    day2 = slice_for_date([raw], date(2026, 7, 23), IST)
    assert len(day1) == 1 and len(day2) == 1
    assert day1[0].user_messages == ["before midnight"]
    assert day2[0].user_messages == ["after midnight"]
    assert day1[0].tokens_in == 100
    assert day2[0].tokens_in == 40
    # The 35-minute gap around midnight exceeds the idle cutoff and is not
    # reported as active work.
    assert abs(day1[0].duration_minutes + day2[0].duration_minutes - 25.0) < 0.01


def test_idle_middle_day_is_not_reported_as_activity():
    first = datetime(2026, 7, 21, 23, 0, tzinfo=IST)
    last = datetime(2026, 7, 23, 9, 0, tzinfo=IST)
    raw = RawSession(
        session_id="idle-gap",
        project_path="/proj",
        source="codex",
        start_time=first,
        end_time=last,
        events=[_ev(first, user_message="start"), _ev(last, user_message="resume")],
    )

    assert slice_for_date([raw], date(2026, 7, 22), IST) == []


def test_total_active_minutes_unions_overlapping_sessions():
    from devlog.models import SessionDigest

    start = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
    a = SessionDigest(
        "a",
        "/p1",
        "codex",
        start,
        start + timedelta(minutes=10),
        active_minutes=10,
        active_intervals=[(start, start + timedelta(minutes=10))],
    )
    b = SessionDigest(
        "b",
        "/p2",
        "cursor",
        start + timedelta(minutes=5),
        start + timedelta(minutes=15),
        active_minutes=10,
        active_intervals=[
            (start + timedelta(minutes=5), start + timedelta(minutes=15))
        ],
    )

    assert total_active_minutes([a, b]) == 15


def test_single_event_session_falls_back_to_session_span_not_zero():
    # Only one event lands within the day, so there's no gap to measure an
    # active interval from -- but the session clearly wasn't idle for 0
    # minutes, so duration should fall back to the clipped session span
    # instead of being silently reported as 0.
    start = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
    event_time = datetime(2026, 7, 22, 9, 5, tzinfo=UTC)
    end = datetime(2026, 7, 22, 9, 10, tzinfo=UTC)
    raw = RawSession(
        session_id="single-event",
        project_path="/proj",
        source="claude_code",
        start_time=start,
        end_time=end,
        events=[_ev(event_time, user_message="only message")],
    )

    digests = slice_for_date([raw], date(2026, 7, 22), UTC)

    assert len(digests) == 1
    assert digests[0].active_minutes is None
    assert digests[0].duration_minutes == 10.0


def test_no_overlap_returns_empty():
    ts = datetime(2026, 7, 21, 12, 0, tzinfo=IST)
    raw = RawSession(
        session_id="s",
        project_path="/p",
        source="claude_code",
        start_time=ts,
        end_time=ts,
        events=[_ev(ts, user_message="old")],
    )
    assert slice_for_date([raw], date(2026, 7, 22), IST) == []


def test_build_raw_digest_lists_projects():
    start = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
    end = datetime(2026, 7, 22, 9, 15, tzinfo=UTC)
    from devlog.models import SessionDigest

    d = SessionDigest(
        session_id="s",
        project_path="/Users/dev/code/variantgpt",
        source="claude_code",
        start_time=start,
        end_time=end,
        user_messages=["Refactor VCF parser"],
        tool_calls={"Edit": 1},
        files_touched={"/Users/dev/code/variantgpt/parsers/vcf_parser.py"},
    )
    text = build_raw_digest([d])
    assert "variantgpt" in text
    assert "Refactor VCF parser" in text


def test_compact_digest_is_shorter_and_keeps_signal():
    start = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
    end = datetime(2026, 7, 22, 9, 15, tzinfo=UTC)
    from devlog.models import SessionDigest

    d = SessionDigest(
        session_id="s",
        project_path="/Users/dev/code/variantgpt",
        source="claude_code",
        start_time=start,
        end_time=end,
        user_messages=[
            "Refactor the VCF parser so it streams",
            "Add a progress bar",
            "Also do something else that should be dropped in compact mode",
        ],
        tool_calls={"Edit": 1},
        files_touched={"/Users/dev/code/variantgpt/parsers/vcf_parser.py"},
        bash_commands=["pytest tests/test_vcf_parser.py -q"],
    )
    full = build_raw_digest([d])
    compact = build_raw_digest([d], compact=True)
    assert len(compact) < len(full)
    assert "variantgpt" in compact
    assert "Refactor the VCF parser" in compact
    assert "should be dropped" not in compact
    assert "src=claude_code" in compact
    assert "source=" not in compact
    assert "vcf_parser.py" in compact
