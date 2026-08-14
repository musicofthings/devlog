from pathlib import Path

from devlog.sources.copilot import CopilotParser, parse_events_file

FIXTURES = Path(__file__).resolve().parents[1] / "sample_data" / "copilot"


def test_sample_fixtures_parse():
    sessions = CopilotParser().iter_sessions(FIXTURES)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.source == "copilot"
    assert s.session_id == "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    assert "vitreous" in s.project_path.replace("\\", "/").lower()
    msgs = [e.user_message for e in s.events if e.user_message]
    assert any("consent gate" in m for m in msgs)
    assert any("broker tests" in m for m in msgs)
    assert not any("skill-context" in (m or "") for m in msgs)
    assert any(e.tool_name == "powershell" for e in s.events)
    assert any(e.tool_name == "view" for e in s.events)
    assert any(e.bash_command and "git status" in e.bash_command for e in s.events)
    assert any(e.file_path and e.file_path.endswith("consent.ts") for e in s.events)


def test_malformed_line_skipped(tmp_path: Path):
    sess = tmp_path / "session-state" / "sid"
    sess.mkdir(parents=True)
    path = sess / "events.jsonl"
    path.write_text(
        '{"type":"session.start","data":{"sessionId":"sid","context":{"cwd":"/tmp/p"}},'
        '"timestamp":"2026-07-20T12:00:00Z"}\n'
        "NOT JSON\n"
        '{"type":"user.message","data":{"content":"hello copilot","source":"user"},'
        '"timestamp":"2026-07-20T12:00:01Z"}\n',
        encoding="utf-8",
    )
    session = parse_events_file(path)
    assert session is not None
    assert session.project_path.replace("\\", "/") == "/tmp/p"
    assert session.events[0].user_message == "hello copilot"


def test_skips_system_and_skill_chrome(tmp_path: Path):
    sess = tmp_path / "session-state" / "sid2"
    sess.mkdir(parents=True)
    path = sess / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"type":"session.start","data":{"context":{"gitRoot":"/tmp/p"}},'
                '"timestamp":"2026-07-20T12:00:00Z"}',
                '{"type":"user.message","data":{"content":"","source":"system"},'
                '"timestamp":"2026-07-20T12:00:01Z"}',
                '{"type":"user.message","data":{"content":"<system_reminder>x</system_reminder>",'
                '"source":"system"},"timestamp":"2026-07-20T12:00:02Z"}',
                '{"type":"user.message","data":{"content":"real task","source":"user"},'
                '"timestamp":"2026-07-20T12:00:03Z"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    session = parse_events_file(path)
    assert session is not None
    msgs = [e.user_message for e in session.events if e.user_message]
    assert msgs == ["real task"]


def test_empty_root_returns_empty(tmp_path: Path):
    assert CopilotParser().iter_sessions(tmp_path) == []
