from pathlib import Path

from devlog.cli import DEFAULT_SOURCES, main


def test_dry_run_does_not_write(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "claude_code"
    code = main(
        [
            "--date",
            "2026-07-22",
            "--sources",
            "claude_code",
            "--claude-root",
            str(sample),
            "--sample-mode",
            "--dry-run",
        ]
    )
    assert code == 0
    assert list(tmp_path.glob("devlog-*.md")) == []
    out = capsys.readouterr().out
    assert "Daily post" in out or "variantgpt" in out.lower() or "session" in out.lower()


def test_write_creates_file(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "claude_code"
    code = main(
        [
            "--date",
            "2026-07-22",
            "--sources",
            "claude_code",
            "--claude-root",
            str(sample),
            "--sample-mode",
        ]
    )
    assert code == 0
    assert (tmp_path / "devlog-2026-07-22.md").exists()


def test_unknown_source_exits_2(capsys):
    code = main(["--sources", "nope", "--dry-run", "--date", "2026-07-22"])
    assert code == 2
    err = capsys.readouterr().out
    assert err.startswith("Unknown source(s): nope")
    assert "'" not in err.split("\n")[0]


def test_bad_date_exits_2(capsys):
    code = main(["--date", "not-a-date", "--dry-run"])
    assert code == 2
    assert "Invalid --date" in capsys.readouterr().out


def test_default_sources_include_all_three():
    assert DEFAULT_SOURCES == "claude_code,codex,cursor"


def test_missing_root_skips_source_continues(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "claude_code"
    missing = tmp_path / "no-such-codex"
    code = main(
        [
            "--date",
            "2026-07-22",
            "--sources",
            "claude_code,codex",
            "--claude-root",
            str(sample),
            "--codex-root",
            str(missing),
            "--dry-run",
            "--verbose",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "[codex] no data root" in out
    assert "Daily post" in out


def test_multi_source_roots(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    root = Path(__file__).resolve().parents[1] / "sample_data"
    code = main(
        [
            "--date",
            "2026-07-20",
            "--sources",
            "codex,cursor",
            "--codex-root",
            str(root / "codex"),
            "--cursor-root",
            str(root / "cursor"),
            "--dry-run",
            "--verbose",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "[codex] found" in out
    assert "[cursor] found" in out
    assert "session" in out.lower() or "Daily post" in out
