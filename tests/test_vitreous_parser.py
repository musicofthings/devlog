from pathlib import Path

from devlog.sources.vitreous import VitreousParser, parse_session_file

FIXTURES = Path(__file__).resolve().parents[1] / "sample_data" / "vitreous"


def test_sample_jsonl_when_present():
    sessions = VitreousParser().iter_sessions(FIXTURES)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.source == "vitreous"
    assert any(e.user_message and "Persist vitreous" in e.user_message for e in s.events)
    assert any(e.tool_name == "Read" for e in s.events)
    assert any(e.tool_name == "Shell" for e in s.events)
    assert any(e.bash_command and "pytest" in e.bash_command for e in s.events)


def test_skips_nvidia_skills_cache(tmp_path: Path):
    cache = tmp_path / "nvidia-skills" / "sessions"
    cache.mkdir(parents=True)
    (cache / "not-a-session.jsonl").write_text(
        '{"timestamp":"2026-07-20T12:00:00Z","type":"user","content":"from skills cache"}\n',
        encoding="utf-8",
    )
    assert VitreousParser().iter_sessions(tmp_path) == []


def test_malformed_line_skipped(tmp_path: Path):
    sess_dir = tmp_path / "sessions"
    sess_dir.mkdir()
    path = sess_dir / "s.jsonl"
    path.write_text(
        '{"timestamp":"2026-07-20T12:00:00Z","type":"user","content":"ok vitreous"}\n'
        "NOT JSON\n"
        '{"timestamp":"2026-07-20T12:00:01Z","type":"assistant","tool_name":"Read",'
        '"path":"/tmp/a.py"}\n',
        encoding="utf-8",
    )
    session = parse_session_file(path)
    assert session is not None
    assert session.events[0].user_message == "ok vitreous"
    assert any(e.tool_name == "Read" for e in session.events)


def test_empty_root_returns_empty(tmp_path: Path):
    assert VitreousParser().iter_sessions(tmp_path) == []
