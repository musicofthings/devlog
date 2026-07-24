import json
from pathlib import Path

from devlog.sources.claude_code import ClaudeCodeParser, resolve_project_path

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
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "/Users/dev/code/variant-caller/main.py"},
                    }
                ],
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
        "NOT JSON\n"
        '{"type":"assistant","timestamp":"2026-07-22T10:01:00Z","message":{"content":[],"usage":{"input_tokens":1,"output_tokens":1,"cache_read_input_tokens":0}}}\n',
        encoding="utf-8",
    )
    raw = ClaudeCodeParser().iter_sessions(tmp_path)
    assert len(raw) == 1
    assert raw[0].events[0].user_message == "ok"


def test_resolve_path_fallback_order():
    assert (
        resolve_project_path(
            cwd="/real/variant-caller",
            files=["/real/variant-caller/a.py"],
            folder_name="-Users-dev-code-variant-caller",
        )
        == "/real/variant-caller"
    )


def test_resolve_path_prefers_folder_decode_over_nested_common_root():
    # variantgpt-style: no cwd captured, and every touched file lives inside
    # a subdirectory (parsers/) of the real project root. The common root of
    # the file paths is therefore a strict subdirectory of the folder-decoded
    # path and would be a worse (too-deep) answer than the decode.
    assert (
        resolve_project_path(
            cwd=None,
            files=[
                "/Users/dev/code/variantgpt/parsers/vcf_parser.py",
                "/Users/dev/code/variantgpt/parsers/bed_parser.py",
            ],
            folder_name="-Users-dev-code-variantgpt",
        )
        == "/Users/dev/code/variantgpt"
    )


def test_resolve_path_uses_common_root_when_ancestor_of_folder_decode():
    # Common root of touched files is *shallower than or equal to* the
    # folder decode (not a strict subdirectory), so it's still safe to use.
    assert (
        resolve_project_path(
            cwd=None,
            files=["/Users/dev/code/variantgpt/main.py", "/Users/dev/code/variantgpt/utils.py"],
            folder_name="-Users-dev-code-variantgpt",
        )
        == "/Users/dev/code/variantgpt"
    )
