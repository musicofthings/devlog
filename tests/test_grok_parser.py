from pathlib import Path
from urllib.parse import unquote

from devlog.sources.grok import GrokParser, decode_grok_cwd_folder, parse_session_dir

FIXTURES = Path(__file__).resolve().parents[1] / "sample_data" / "grok"


def test_decode_url_encoded_windows_cwd():
    encoded = "C%3A%5CUsers%5Cdev%5Ccode%5Cdevlog"
    assert decode_grok_cwd_folder(encoded).replace("\\", "/") == "C:/Users/dev/code/devlog"
    assert unquote(encoded).replace("\\", "/") == "C:/Users/dev/code/devlog"


def test_sample_fixtures_parse():
    sessions = GrokParser().iter_sessions(FIXTURES)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.source == "grok"
    assert s.session_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert s.project_path.replace("\\", "/") == "C:/Users/dev/code/devlog"
    msgs = [e.user_message for e in s.events if e.user_message]
    assert any("Grok CLI source parser" in m for m in msgs)
    assert any("DEFAULT_SOURCES" in m for m in msgs)
    assert not any("Available skills" in (m or "") for m in msgs)
    assert not any("You are Grok" in (m or "") for m in msgs)
    assert any(e.tool_name == "read_file" for e in s.events)
    assert any(e.tool_name == "run_terminal_command" for e in s.events)
    assert any(e.file_path and e.file_path.endswith("grok.py") for e in s.events)
    assert any(e.bash_command and "pytest" in e.bash_command for e in s.events)


def test_prefers_summary_cwd_over_folder_decode(tmp_path: Path):
    encoded = "C%3A%5CUsers%5Cdev%5Ccode%5Cwrong"
    sess = tmp_path / "sessions" / encoded / "sid-1"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text(
        '{"info":{"id":"sid-1","cwd":"C:\\\\Users\\\\dev\\\\code\\\\right"},'
        '"created_at":"2026-07-20T10:00:00Z","git_root_dir":"C:/Users/dev/code/right/"}\n',
        encoding="utf-8",
    )
    (sess / "chat_history.jsonl").write_text(
        '{"type":"user","content":"<user_query>hello grok</user_query>"}\n',
        encoding="utf-8",
    )
    session = parse_session_dir(sess)
    assert session is not None
    assert session.project_path.replace("\\", "/") == "C:/Users/dev/code/right"


def test_folder_decode_when_summary_missing(tmp_path: Path):
    encoded = "C%3A%5CUsers%5Cdev%5Ccode%5Cdevlog"
    sess = tmp_path / "sessions" / encoded / "sid-2"
    sess.mkdir(parents=True)
    (sess / "chat_history.jsonl").write_text(
        '{"type":"user","content":"<user_query>no summary</user_query>"}\n',
        encoding="utf-8",
    )
    session = parse_session_dir(sess)
    assert session is not None
    assert session.project_path.replace("\\", "/") == "C:/Users/dev/code/devlog"


def test_malformed_line_skipped(tmp_path: Path):
    sess = tmp_path / "sessions" / "proj" / "sid-3"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text(
        '{"info":{"id":"sid-3","cwd":"/tmp/proj"},"created_at":"2026-07-20T10:00:00Z"}',
        encoding="utf-8",
    )
    (sess / "chat_history.jsonl").write_text(
        '{"type":"user","content":"<user_query>hello grok</user_query>"}\n'
        "NOT JSON\n"
        '{"type":"assistant","content":"ok","tool_calls":[{"id":"c1","name":"grep",'
        '"arguments":"{\\"path\\":\\"/tmp/proj/a.py\\"}"}]}\n',
        encoding="utf-8",
    )
    session = parse_session_dir(sess)
    assert session is not None
    assert session.events[0].user_message == "hello grok"
    assert any(e.tool_name == "grep" for e in session.events)


def test_midnight_events_keep_own_timestamps(tmp_path: Path):
    sess = tmp_path / "sessions" / "proj" / "sid-mid"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text(
        '{"info":{"id":"sid-mid","cwd":"/tmp/span"},'
        '"created_at":"2026-07-22T23:50:00Z","updated_at":"2026-07-23T00:20:00Z"}',
        encoding="utf-8",
    )
    (sess / "chat_history.jsonl").write_text(
        '{"type":"user","content":"<user_query>cross midnight</user_query>"}\n'
        '{"type":"assistant","tool_calls":['
        '{"id":"c-early","name":"read_file","arguments":"{\\"target_file\\":\\"/tmp/span/a.py\\"}"},'
        '{"id":"c-late","name":"read_file","arguments":"{\\"target_file\\":\\"/tmp/span/b.py\\"}"}'
        "]}\n",
        encoding="utf-8",
    )
    (sess / "events.jsonl").write_text(
        '{"ts":"2026-07-22T23:55:00Z","type":"tool_completed","tool_name":"read_file",'
        '"tool_call_id":"c-early"}\n'
        '{"ts":"2026-07-23T00:10:00Z","type":"tool_completed","tool_name":"read_file",'
        '"tool_call_id":"c-late"}\n',
        encoding="utf-8",
    )
    session = parse_session_dir(sess)
    assert session is not None
    tool_ts = [e.timestamp.isoformat() for e in session.events if e.tool_name]
    assert any("2026-07-22T23:55" in t for t in tool_ts)
    assert any("2026-07-23T00:10" in t for t in tool_ts)


def test_empty_root_returns_empty(tmp_path: Path):
    assert GrokParser().iter_sessions(tmp_path) == []
