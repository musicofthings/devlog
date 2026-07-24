import json
from pathlib import Path

from devlog.sources.codex import CodexParser, parse_rollout_file

FIXTURES = Path(__file__).resolve().parents[1] / "sample_data" / "codex"


def test_sample_fixtures_resolve_cwd():
    sessions = CodexParser().iter_sessions(FIXTURES)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.source == "codex"
    assert s.project_path.replace("/", "\\") == r"C:\Users\dev\code\gurukul"
    assert any(e.user_message and "Gurukul" in e.user_message for e in s.events)
    assert any(e.tool_name == "exec" for e in s.events)
    assert any(e.tool_name == "apply_patch" for e in s.events)
    assert any(e.tokens_in == 1200 for e in s.events)


def test_malformed_line_skipped(tmp_path: Path):
    day = tmp_path / "sessions" / "2026" / "07" / "20"
    day.mkdir(parents=True)
    path = day / "rollout-2026-07-20T12-00-00-abc.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-07-20T12:00:00Z",
                        "type": "session_meta",
                        "payload": {"session_id": "abc", "cwd": "/tmp/proj"},
                    }
                ),
                "NOT JSON",
                json.dumps(
                    {
                        "timestamp": "2026-07-20T12:01:00Z",
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "hello codex"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    session = parse_rollout_file(path)
    assert session is not None
    assert session.project_path == "/tmp/proj"
    assert session.events[0].user_message == "hello codex"


def test_skips_environment_chrome(tmp_path: Path):
    day = tmp_path / "sessions" / "2026" / "07" / "20"
    day.mkdir(parents=True)
    path = day / "rollout-x.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-07-20T12:00:00Z",
                        "type": "session_meta",
                        "payload": {"session_id": "x", "cwd": "/p"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-20T12:00:01Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "<environment_context>noise</environment_context>",
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-07-20T12:00:02Z",
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "real task"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    session = parse_rollout_file(path)
    assert session is not None
    msgs = [e.user_message for e in session.events if e.user_message]
    assert msgs == ["real task"]
