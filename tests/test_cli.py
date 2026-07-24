from pathlib import Path
from devlog.cli import main

def test_dry_run_does_not_write(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "claude_code"
    code = main([
        "--date", "2026-07-22",
        "--claude-root", str(sample),
        "--sample-mode",
        "--dry-run",
    ])
    assert code == 0
    assert list(tmp_path.glob("devlog-*.md")) == []
    out = capsys.readouterr().out
    assert "Daily post" in out or "variantgpt" in out.lower() or "session" in out.lower()

def test_write_creates_file(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "claude_code"
    code = main([
        "--date", "2026-07-22",
        "--claude-root", str(sample),
        "--sample-mode",
    ])
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
