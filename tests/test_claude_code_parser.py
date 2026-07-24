import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from devlog.sources.claude_code import ClaudeCodeParser, resolve_project_path
from devlog.digest import slice_for_date

FIXTURES = Path(__file__).resolve().parents[1] / "sample_data" / "claude_code"


def test_cwd_preferred_over_folder_decode(tmp_path: Path):
    proj = tmp_path / "projects" / "-Users-dev-code-variant-caller"
    proj.mkdir(parents=True)
    session = proj / "s1.jsonl"
    lines = [
        {
            "type": "user",
            "cwd": "/Users/dev/code/variant-caller",
            "timestamp": "2026-07-22T10:00:00Z",
            "message": {"role": "user", "content": "Add tests"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-07-22T10:01:00Z",
            "message": {
                "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/Users/dev/code/variant-caller/main.py"}}],
                "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0},
            },
        },
    ]
    session.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    raw = ClaudeCodeParser().iter_sessions(tmp_path)
    assert len(raw) == 1
    assert raw[0].project_path == "/Users/dev/code/variant-caller"


def test_malformed_line_skipped(tmp_path: Path):
    proj = tmp_path / "projects" / "-Users-dev-code-x"
    proj.mkdir(parents=True)
    (proj / "s.jsonl").write_text(
        '{"type":"user","timestamp":"2026-07-22T10:00:00Z","message":{"content":"ok"}}\n'
        'NOT JSON\n'
        '{"type":"assistant","timestamp":"2026-07-22T10:01:00Z","message":{"content":[],"usage":{"input_tokens":1,"output_tokens":1,"cache_read_input_tokens":0}}}\n',
        encoding="utf-8",
    )
    raw = ClaudeCodeParser().iter_sessions(tmp_path)
    assert len(raw) == 1
    assert raw[0].events[0].user_message == "ok"


def test_resolve_path_fallback_order():
    assert resolve_project_path(
        cwd="/real/variant-caller",
        files=["/real/variant-caller/a.py"],
        folder_name="-Users-dev-code-variant-caller",
    ) == "/real/variant-caller"
