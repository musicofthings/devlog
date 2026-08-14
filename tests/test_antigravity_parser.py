from pathlib import Path

from devlog.sources.antigravity import AntigravityParser


def test_missing_root_returns_empty(tmp_path: Path):
    assert AntigravityParser().iter_sessions(tmp_path / "missing") == []


def test_empty_root_returns_empty(tmp_path: Path):
    root = tmp_path / "gemini"
    root.mkdir()
    (root / "antigravity-cli").mkdir()
    assert AntigravityParser().iter_sessions(root) == []


def test_plaintext_jsonl_parsed_when_present(tmp_path: Path):
    root = tmp_path / "gemini"
    d = root / "antigravity-cli"
    d.mkdir(parents=True)
    (d / "session.jsonl").write_text(
        '{"timestamp":"2026-07-20T12:00:00Z","type":"user","content":"hello antigravity"}\n'
        '{"timestamp":"2026-07-20T12:00:01Z","type":"assistant","tool_name":"Read",'
        '"path":"/tmp/a.py"}\n',
        encoding="utf-8",
    )
    sessions = AntigravityParser().iter_sessions(root)
    assert len(sessions) == 1
    assert sessions[0].source == "antigravity"
    assert any(e.user_message == "hello antigravity" for e in sessions[0].events)


def test_skips_non_json_blobs(tmp_path: Path):
    root = tmp_path / "gemini"
    d = root / "antigravity-ide"
    d.mkdir(parents=True)
    (d / "conversation.pb").write_bytes(b"\x00\x01encrypted-not-json")
    assert AntigravityParser().iter_sessions(root) == []
