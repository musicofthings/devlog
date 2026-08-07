from datetime import date
from pathlib import Path

import pytest

from devlog.config import DevlogConfig, save_config
from devlog.delete_cmd import cmd_delete, delete_day
from devlog.site import rebuild_site, write_post_markdown


def _repo_with_post(tmp_path: Path, day: date, body: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "docs" / "index.html").write_text(
        '<a href="https://github.com/musicofthings/devlog">Open on GitHub →</a>\n',
        encoding="utf-8",
    )
    write_post_markdown(repo / "posts", day, body)
    rebuild_site(repo)
    return repo


def test_delete_missing_post_raises(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = DevlogConfig(repo_path=str(repo).replace("\\", "/"))

    with pytest.raises(FileNotFoundError, match="2026-07-20"):
        delete_day(cfg, date(2026, 7, 20))


def test_delete_dry_run_touches_nothing(tmp_path: Path):
    repo = _repo_with_post(tmp_path, date(2026, 7, 20), "Built the parser.")
    cfg = DevlogConfig(repo_path=str(repo).replace("\\", "/"))

    out = delete_day(cfg, date(2026, 7, 20), dry_run=True)

    assert out["status"] == "dry_run"
    assert (repo / "posts" / "2026-07-20.md").exists()


def test_delete_removes_post_prunes_html_and_pushes(tmp_path: Path):
    repo = _repo_with_post(tmp_path, date(2026, 7, 20), "Built the parser.")
    cfg = DevlogConfig(repo_path=str(repo).replace("\\", "/"), remote="origin", branch="main")
    calls: list[list[str]] = []

    def fake_git(cmd: list[str], cwd: Path):
        calls.append(cmd)
        from subprocess import CompletedProcess

        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="D posts/2026-07-20.md\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    out = delete_day(cfg, date(2026, 7, 20), git_run=fake_git)

    assert out["status"] == "deleted"
    assert not (repo / "posts" / "2026-07-20.md").exists()
    assert not (repo / "docs" / "log" / "2026-07-20.html").exists()
    feed = (repo / "docs" / "log" / "index.html").read_text(encoding="utf-8")
    assert "2026-07-20" not in feed
    commit_call = next(c for c in calls if c[:2] == ["git", "commit"])
    assert commit_call[-1] == "delete: devlog 2026-07-20"
    assert any(c[:2] == ["git", "push"] for c in calls)


def test_delete_raises_if_nothing_to_commit(tmp_path: Path):
    """Guards against a silently-empty commit if the post was never tracked by git."""
    repo = _repo_with_post(tmp_path, date(2026, 7, 20), "Built the parser.")
    cfg = DevlogConfig(repo_path=str(repo).replace("\\", "/"))

    def fake_git(cmd: list[str], cwd: Path):
        from subprocess import CompletedProcess

        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    with pytest.raises(RuntimeError, match="No generated changes") as excinfo:
        delete_day(cfg, date(2026, 7, 20), git_run=fake_git)
    assert "already removed from the working tree" in str(excinfo.value)


def test_cmd_delete_missing_post_exits_2(tmp_path: Path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg_path = tmp_path / "config.toml"
    save_config(DevlogConfig(repo_path=str(repo).replace("\\", "/")), cfg_path)

    code = cmd_delete(["--date", "2026-07-20", "--config", str(cfg_path)])

    assert code == 2
    assert "2026-07-20" in capsys.readouterr().out
    assert not (repo / "posts").exists()


def test_cmd_delete_invalid_date_exits_2(tmp_path: Path, capsys):
    repo = _repo_with_post(tmp_path, date(2026, 7, 20), "Built the parser.")
    cfg_path = tmp_path / "config.toml"
    save_config(DevlogConfig(repo_path=str(repo).replace("\\", "/")), cfg_path)

    code = cmd_delete(["--date", "not-a-date", "--config", str(cfg_path)])

    assert code == 2
    assert "Invalid --date" in capsys.readouterr().out


def test_cmd_delete_no_config_exits_2(tmp_path: Path, capsys):
    missing_cfg = tmp_path / "nope.toml"

    code = cmd_delete(["--date", "2026-07-20", "--config", str(missing_cfg)])

    assert code == 2
    assert "No config" in capsys.readouterr().out


def test_cmd_delete_dry_run_prints_status(tmp_path: Path, capsys):
    repo = _repo_with_post(tmp_path, date(2026, 7, 20), "Built the parser.")
    cfg_path = tmp_path / "config.toml"
    save_config(DevlogConfig(repo_path=str(repo).replace("\\", "/")), cfg_path)

    code = cmd_delete(["--date", "2026-07-20", "--dry-run", "--config", str(cfg_path)])

    assert code == 0
    assert "dry_run: 2026-07-20" in capsys.readouterr().out
