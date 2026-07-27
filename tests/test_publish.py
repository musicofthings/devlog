from __future__ import annotations

from datetime import date
from pathlib import Path

from devlog.config import DevlogConfig, load_config, save_config
from devlog.init_cmd import cmd_init
from devlog.publish import publish_day, resolve_publish_date
from devlog.site import list_posts, rebuild_site, write_post_markdown


def test_save_and_load_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    cfg = DevlogConfig(publish_mode="manual", repo_path=str(tmp_path).replace("\\", "/"))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded is not None
    assert loaded.publish_mode == "manual"
    assert loaded.sources == ["claude_code", "codex", "cursor"]


def test_init_defaults(tmp_path: Path):
    cfg_path = tmp_path / "devlog" / "config.toml"
    code = cmd_init(["--defaults", "--no-schedule", "--config", str(cfg_path)])
    assert code == 0
    assert cfg_path.exists()
    loaded = load_config(cfg_path)
    assert loaded is not None
    # Privacy-safe default: posts can contain raw prompts, so no auto-publish.
    assert loaded.publish_mode == "manual"


def test_config_windows_paths_roundtrip(tmp_path: Path):
    """Backslash paths must not corrupt the TOML file (\\U is an invalid escape)."""
    path = tmp_path / "config.toml"
    cfg = DevlogConfig(
        claude_root=r"C:\Users\shibi\.claude",
        codex_root=r"C:\Users\shibi\.codex",
        cursor_root=r"C:\Users\shibi\.cursor",
        repo_path=r"C:\Users\shibi\Projects\devlog",
        publish_mode="manual",
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded is not None
    assert loaded.claude_root == "C:/Users/shibi/.claude"
    assert loaded.repo_path == "C:/Users/shibi/Projects/devlog"


def test_config_rejects_bad_schedule_time(tmp_path: Path):
    import pytest

    with pytest.raises(ValueError):
        DevlogConfig(schedule_time="6.30").validate()
    with pytest.raises(ValueError):
        DevlogConfig(schedule_time="25:00").validate()
    DevlogConfig(schedule_time="06:30").validate()


def test_resolve_publish_date():
    today = date(2026, 7, 24)
    assert resolve_publish_date("yesterday", today=today) == date(2026, 7, 23)
    assert resolve_publish_date("today", today=today) == today
    assert resolve_publish_date("2026-07-20", today=today) == date(2026, 7, 20)


def test_site_builder(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "index.html").write_text(
        '<a href="https://github.com/musicofthings/devlog">Open on GitHub →</a>\n',
        encoding="utf-8",
    )
    write_post_markdown(repo / "posts", date(2026, 7, 20), "Built the Codex parser today.")
    write_post_markdown(repo / "posts", date(2026, 7, 21), "Wired Cursor agent transcripts.")
    written = rebuild_site(repo)
    assert (repo / "docs" / ".nojekyll").exists()
    assert (repo / "docs" / "log" / "index.html").exists()
    assert (repo / "docs" / "log" / "2026-07-20.html").exists()
    assert (repo / "docs" / "log" / "2026-07-21.html").exists()
    feed = (repo / "docs" / "log" / "index.html").read_text(encoding="utf-8")
    assert "2026-07-21" in feed
    assert "2026-07-20" in feed
    assert feed.index("2026-07-21") < feed.index("2026-07-20")
    landing = (repo / "docs" / "index.html").read_text(encoding="utf-8")
    assert "log/index.html" in landing
    assert written


def test_publish_idempotent_skip(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "index.html").write_text(
        '<a href="https://github.com/musicofthings/devlog">Open on GitHub →</a>\n',
        encoding="utf-8",
    )
    posts = repo / "posts"
    write_post_markdown(posts, date(2026, 7, 20), "Already published.")

    cfg = DevlogConfig(
        sources=["codex"],
        codex_root=str(Path(__file__).resolve().parents[1] / "sample_data" / "codex"),
        repo_path=str(repo).replace("\\", "/"),
        publish_mode="manual",
    )
    out = publish_day(cfg, date(2026, 7, 20), force=False)
    assert out["status"] == "skipped"


def test_publish_manual_writes(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "index.html").write_text(
        '<a href="https://github.com/musicofthings/devlog">Open on GitHub →</a>\n',
        encoding="utf-8",
    )
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "codex"
    cfg = DevlogConfig(
        sources=["codex"],
        codex_root=str(sample),
        repo_path=str(repo).replace("\\", "/"),
        publish_mode="manual",
    )
    out = publish_day(cfg, date(2026, 7, 20), force=True)
    assert out["status"] == "written"
    assert (repo / "posts" / "2026-07-20.md").exists()
    assert (repo / "docs" / "log" / "2026-07-20.html").exists()
    assert (repo / "docs" / "log" / "index.html").exists()


def test_publish_auto_calls_git(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "index.html").write_text(
        '<a href="https://github.com/musicofthings/devlog">Open on GitHub →</a>\n',
        encoding="utf-8",
    )
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "codex"
    cfg = DevlogConfig(
        sources=["codex"],
        codex_root=str(sample),
        repo_path=str(repo).replace("\\", "/"),
        publish_mode="auto",
        remote="origin",
        branch="main",
    )
    calls: list[list[str]] = []

    def fake_git(cmd: list[str], cwd: Path):
        calls.append(cmd)
        from subprocess import CompletedProcess

        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="M posts/2026-07-20.md\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    out = publish_day(cfg, date(2026, 7, 20), force=True, git_run=fake_git)
    assert out["status"] == "published_auto"
    assert any(c[:2] == ["git", "add"] for c in calls)
    assert any(c[:2] == ["git", "commit"] for c in calls)
    # Pull --rebase must happen after commit and before push, so a moved
    # remote doesn't permanently break the nightly job.
    pull_idx = next(i for i, c in enumerate(calls) if c[:3] == ["git", "pull", "--rebase"])
    push_idx = next(i for i, c in enumerate(calls) if c[:2] == ["git", "push"])
    commit_idx = next(i for i, c in enumerate(calls) if c[:2] == ["git", "commit"])
    assert commit_idx < pull_idx < push_idx


def test_publish_pr_branches_from_base_and_returns(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "index.html").write_text(
        '<a href="https://github.com/musicofthings/devlog">Open on GitHub →</a>\n',
        encoding="utf-8",
    )
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "codex"
    cfg = DevlogConfig(
        sources=["codex"],
        codex_root=str(sample),
        repo_path=str(repo).replace("\\", "/"),
        publish_mode="pr",
        remote="origin",
        branch="main",
    )
    calls: list[list[str]] = []

    def fake_git(cmd: list[str], cwd: Path):
        calls.append(cmd)
        from subprocess import CompletedProcess

        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="M posts/2026-07-20.md\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    out = publish_day(cfg, date(2026, 7, 20), force=True, git_run=fake_git, gh_run=fake_git)
    assert out["status"] == "published_pr"
    # Branch is created off main, not off whatever was checked out.
    assert ["git", "checkout", "-B", "devlog/post-2026-07-20", "main"] in calls
    # And the repo ends back on main so tomorrow's PR doesn't stack.
    assert calls[-1] == ["git", "checkout", "main"]


def test_publish_dry_run_no_files(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "codex"
    cfg = DevlogConfig(
        sources=["codex"],
        codex_root=str(sample),
        repo_path=str(repo).replace("\\", "/"),
        publish_mode="auto",
    )
    out = publish_day(cfg, date(2026, 7, 20), dry_run=True)
    assert out["status"] == "dry_run"
    assert not (repo / "posts").exists()
    assert out["sessions"] == 1
    assert "gurukul" in out["post"].lower()


def test_list_posts_newest_first(tmp_path: Path):
    posts = tmp_path / "posts"
    write_post_markdown(posts, date(2026, 7, 20), "a")
    write_post_markdown(posts, date(2026, 7, 22), "b")
    items = list_posts(posts)
    assert [d.isoformat() for d, _, _ in items] == ["2026-07-22", "2026-07-20"]
