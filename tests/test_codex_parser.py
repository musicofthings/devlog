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


def test_invalid_token_shapes_do_not_abort_rollout(tmp_path: Path):
    day = tmp_path / "sessions" / "2026" / "07" / "20"
    day.mkdir(parents=True)
    path = day / "rollout-invalid-shape.jsonl"
    lines = [
        {
            "timestamp": 123,
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "bad timestamp"},
        },
        {
            "timestamp": "2026-07-20T12:00:00Z",
            "type": "event_msg",
            "payload": {"type": "token_count", "info": "bad"},
        },
        {
            "timestamp": "2026-07-20T12:01:00Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "valid task"},
        },
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    session = parse_rollout_file(path)

    assert session is not None
    assert [event.user_message for event in session.events if event.user_message] == ["valid task"]


def test_total_token_usage_summed_as_deltas(tmp_path: Path):
    """Cumulative total_token_usage must not be summed as-is across events."""
    day = tmp_path / "sessions" / "2026" / "07" / "20"
    day.mkdir(parents=True)
    path = day / "rollout-totals.jsonl"

    def token_event(minute: int, tokens_in: int, tokens_out: int) -> str:
        return json.dumps(
            {
                "timestamp": f"2026-07-20T12:{minute:02d}:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": tokens_in,
                            "output_tokens": tokens_out,
                            "cached_input_tokens": 0,
                        }
                    },
                },
            }
        )

    lines = [
        json.dumps(
            {
                "timestamp": "2026-07-20T12:00:00Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "task"},
            }
        ),
        token_event(1, 1000, 100),
        token_event(2, 2500, 250),
        token_event(3, 4000, 400),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    session = parse_rollout_file(path)
    assert session is not None
    assert sum(e.tokens_in for e in session.events) == 4000
    assert sum(e.tokens_out for e in session.events) == 400


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
