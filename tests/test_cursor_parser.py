from datetime import UTC
from pathlib import Path

from devlog.sources.cursor import CursorParser, decode_cursor_project_folder, parse_transcript_file

FIXTURES = Path(__file__).resolve().parents[1] / "sample_data" / "cursor"


def test_decode_windows_drive_folder():
    assert decode_cursor_project_folder("c-Users-dev-code-devlog") == "C:/Users/dev/code/devlog"


def test_sample_fixtures_parse():
    sessions = CursorParser().iter_sessions(FIXTURES)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.source == "cursor"
    assert s.project_path == "C:/Users/dev/code/devlog"
    assert s.session_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    msgs = [e.user_message for e in s.events if e.user_message]
    assert any("Cursor source parser" in m for m in msgs)
    assert any(e.tool_name == "Read" for e in s.events)
    assert any(e.tool_name == "Shell" for e in s.events)
    assert any(e.bash_command and "pytest" in e.bash_command for e in s.events)


def test_embedded_timestamp_generic_utc_offsets():
    from datetime import timedelta

    from devlog.sources.cursor import _parse_embedded_timestamp

    dt = _parse_embedded_timestamp(
        "<timestamp>Monday, Jul 20, 2026, 1:00 PM (UTC-7)</timestamp>hello"
    )
    assert dt is not None
    assert dt.utcoffset() == timedelta(hours=-7)

    dt = _parse_embedded_timestamp(
        "<timestamp>Monday, Jul 20, 2026, 1:00 PM (UTC+5:30)</timestamp>hello"
    )
    assert dt is not None
    assert dt.utcoffset() == timedelta(hours=5, minutes=30)

    dt = _parse_embedded_timestamp("<timestamp>Monday, Jul 20, 2026, 1:00 PM (UTC)</timestamp>x")
    assert dt is not None
    assert dt.tzinfo == UTC


def test_malformed_line_skipped(tmp_path: Path):
    proj = tmp_path / "projects" / "c-Users-dev-code-x"
    tdir = proj / "agent-transcripts" / "sid"
    tdir.mkdir(parents=True)
    path = tdir / "sid.jsonl"
    path.write_text(
        '{"role":"user","message":{"content":[{"type":"text","text":'
        '"<timestamp>Monday, Jul 20, 2026, 1:00 PM (UTC+5:30)</timestamp>\\n'
        '<user_query>\\nhello cursor\\n</user_query>"}]}}\n'
        "NOT JSON\n"
        '{"role":"assistant","message":{"content":[{"type":"tool_use","name":"Glob",'
        '"input":{"glob_pattern":"*.py"}}]}}\n',
        encoding="utf-8",
    )
    session = parse_transcript_file(path, proj.name)
    assert session is not None
    assert session.events[0].user_message == "hello cursor"
    assert any(e.tool_name == "Glob" for e in session.events)
