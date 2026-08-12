"""Tests for soft-hide / unhide."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from devlog.config import DevlogConfig, save_config
from devlog.hidden import hide_date, is_hidden, load_hidden_dates
from devlog.hide_cmd import cmd_hide, cmd_unhide, hide_day, unhide_day
from devlog.site import rebuild_site, write_post_markdown


def _repo_with_posts(tmp_path: Path, *days: date) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "docs" / "index.html").write_text(
        '<a href="https://github.com/musicofthings/devlog">Open on GitHub →</a>\n',
        encoding="utf-8",
    )
    for day in days:
        write_post_markdown(repo / "posts", day, f"Post for {day.isoformat()}.")
    rebuild_site(repo)
    return repo


def test_hide_omits_from_feed_keeps_markdown(tmp_path: Path):
    repo = _repo_with_posts(tmp_path, date(2026, 7, 20), date(2026, 7, 21))
    hide_date(repo, date(2026, 7, 20))
    rebuild_site(repo)

    assert (repo / "posts" / "2026-07-20.md").exists()
    assert not (repo / "docs" / "log" / "2026-07-20.html").exists()
    assert (repo / "docs" / "log" / "2026-07-21.html").exists()
    feed = (repo / "docs" / "log" / "index.html").read_text(encoding="utf-8")
    assert 'href="2026-07-20.html"' not in feed
    assert 'href="2026-07-21.html"' in feed


def test_hide_day_pushes_and_records_status(tmp_path: Path):
    repo = _repo_with_posts(tmp_path, date(2026, 7, 20))
    cfg = DevlogConfig(repo_path=str(repo).replace("\\", "/"), remote="origin", branch="main")
    calls: list[list[str]] = []

    def fake_git(cmd: list[str], cwd: Path):
        calls.append(cmd)
        from subprocess import CompletedProcess

        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="A .devlog-hidden.json\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    out = hide_day(cfg, date(2026, 7, 20), git_run=fake_git)

    assert out["status"] == "hidden"
    assert is_hidden(repo, date(2026, 7, 20))
    assert any(c[:2] == ["git", "push"] for c in calls)
    assert "Last hidden: 2026-07-20" in (repo / "docs" / "log" / "index.html").read_text(
        encoding="utf-8"
    )


def test_unhide_day_restores_feed(tmp_path: Path):
    repo = _repo_with_posts(tmp_path, date(2026, 7, 20))
    hide_date(repo, date(2026, 7, 20))
    rebuild_site(repo)
    cfg = DevlogConfig(repo_path=str(repo).replace("\\", "/"), remote="origin", branch="main")

    def fake_git(cmd: list[str], cwd: Path):
        from subprocess import CompletedProcess

        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="D .devlog-hidden.json\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    out = unhide_day(cfg, date(2026, 7, 20), git_run=fake_git)

    assert out["status"] == "unhidden"
    assert not is_hidden(repo, date(2026, 7, 20))
    assert not load_hidden_dates(repo)
    assert (repo / "docs" / "log" / "2026-07-20.html").exists()


def test_hide_dry_run_and_missing(tmp_path: Path):
    repo = _repo_with_posts(tmp_path, date(2026, 7, 20))
    cfg = DevlogConfig(repo_path=str(repo).replace("\\", "/"))

    dry = hide_day(cfg, date(2026, 7, 20), dry_run=True)
    assert dry["status"] == "dry_run"
    assert not is_hidden(repo, date(2026, 7, 20))

    with pytest.raises(FileNotFoundError):
        hide_day(cfg, date(2026, 1, 1))


def test_cmd_hide_invalid_date(tmp_path: Path, capsys):
    repo = _repo_with_posts(tmp_path, date(2026, 7, 20))
    cfg_path = tmp_path / "config.toml"
    save_config(DevlogConfig(repo_path=str(repo).replace("\\", "/")), cfg_path)

    assert cmd_hide(["--date", "nope", "--config", str(cfg_path)]) == 2
    assert "Invalid --date" in capsys.readouterr().out
    assert cmd_unhide(["--date", "nope", "--config", str(cfg_path)]) == 2


def test_admin_lists_hidden_and_polls_actions(tmp_path: Path):
    repo = _repo_with_posts(tmp_path, date(2026, 7, 20), date(2026, 7, 21))
    hide_date(repo, date(2026, 7, 20))

    def fake_git(cmd, cwd):
        from subprocess import CompletedProcess

        return CompletedProcess(cmd, 0, stdout="git@github.com:someone/theirfork.git\n", stderr="")

    rebuild_site(repo, git_run=fake_git)
    feed = (repo / "docs" / "log" / "index.html").read_text(encoding="utf-8")

    assert 'class="hide-btn" data-date="2026-07-21"' in feed
    assert 'class="unhide-btn" data-date="2026-07-20"' in feed
    assert "pollWorkflowRun" in feed
    assert "workflow_runs" in feed
    assert 'inputs: { date: day, action: action }' in feed
