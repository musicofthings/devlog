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


def test_write_refuses_to_overwrite_edited_post(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    existing = tmp_path / "devlog-2026-07-22.md"
    existing.write_text("manually edited\n", encoding="utf-8")

    code = main(["--date", "2026-07-22"])

    assert code == 1
    assert existing.read_text(encoding="utf-8") == "manually edited\n"


def test_force_replaces_existing_post(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    existing = tmp_path / "devlog-2026-07-22.md"
    existing.write_text("old\n", encoding="utf-8")

    code = main(["--date", "2026-07-22", "--force"])

    assert code == 0
    assert existing.read_text(encoding="utf-8").startswith("# 2026-07-22")


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


def test_default_sources_include_new_plugins():
    assert DEFAULT_SOURCES[:5] == ["claude_code", "codex", "cursor", "grok", "copilot"]
    for name in ("opencode", "warp", "vitreous", "antigravity"):
        assert name in DEFAULT_SOURCES


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


def test_new_source_flags_and_missing_roots(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    root = Path(__file__).resolve().parents[1] / "sample_data"
    missing = tmp_path / "no-warp"
    code = main(
        [
            "--date",
            "2026-07-20",
            "--sources",
            "grok,copilot,warp",
            "--grok-root",
            str(root / "grok"),
            "--copilot-root",
            str(root / "copilot"),
            "--warp-root",
            str(missing),
            "--dry-run",
            "--verbose",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "[grok] found" in out
    assert "[copilot] found" in out
    assert "[warp] no data root" in out
    assert "Daily post" in out


def test_main_dispatches_delete_subcommand(monkeypatch):
    import devlog.delete_cmd

    calls = []

    def fake_cmd_delete(argv):
        calls.append(argv)
        return 0

    monkeypatch.setattr(devlog.delete_cmd, "cmd_delete", fake_cmd_delete)

    code = main(["delete", "--date", "2026-07-20"])

    assert code == 0
    assert calls == [["--date", "2026-07-20"]]


def test_main_dispatches_hide_and_unhide(monkeypatch):
    import devlog.hide_cmd

    hide_calls = []
    unhide_calls = []

    monkeypatch.setattr(
        devlog.hide_cmd, "cmd_hide", lambda argv: hide_calls.append(argv) or 0
    )
    monkeypatch.setattr(
        devlog.hide_cmd, "cmd_unhide", lambda argv: unhide_calls.append(argv) or 0
    )

    assert main(["hide", "--date", "2026-07-20"]) == 0
    assert main(["unhide", "--date", "2026-07-20", "--dry-run"]) == 0
    assert hide_calls == [["--date", "2026-07-20"]]
    assert unhide_calls == [["--date", "2026-07-20", "--dry-run"]]


def test_main_dispatches_obsidian(monkeypatch):
    import devlog.obsidian

    calls = []

    monkeypatch.setattr(
        devlog.obsidian, "cmd_obsidian", lambda argv: calls.append(argv) or 0
    )

    assert main(["obsidian", "--backfill", "--dry-run"]) == 0
    assert calls == [["--backfill", "--dry-run"]]
