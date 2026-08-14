"""Tests for the Obsidian vault mirror."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from devlog.config import DevlogConfig, load_config, save_config
from devlog.obsidian import (
    archive_path,
    backfill_posts,
    create_obsidian_vault,
    daily_path,
    detect_obsidian_vault,
    ensure_obsidian_vault,
    planned_paths,
    register_obsidian_vault,
    remove_mirrored_post,
    render_archive,
    should_remove_from_vault,
    strip_daily_region,
    try_mirror_post,
    upsert_daily_region,
    wikilink_for,
)

DAY = date(2026, 8, 13)
POST = "# 2026-08-13\n\nToday I logged 328 active min across vitreous.\n"


def _cfg(tmp_path: Path, **kwargs) -> DevlogConfig:
    vault = kwargs.pop("vault", tmp_path / "vault")
    vault.mkdir(parents=True, exist_ok=True)
    defaults = {
        "repo_path": str(tmp_path / "repo").replace("\\", "/"),
        "publish_mode": "manual",
        "obsidian_vault": str(vault).replace("\\", "/"),
        "obsidian_folder": "DevLog",
        "obsidian_daily_folder": "Daily",
        "obsidian_on_delete": "preserve",
    }
    defaults.update(kwargs)
    return DevlogConfig(**defaults)


def test_render_archive_adds_frontmatter():
    text = render_archive(DAY, POST)
    assert text.startswith("---\n")
    assert "date: 2026-08-13" in text
    assert "tags:\n  - devlog" in text
    assert "# 2026-08-13\n\nToday I logged 328 active min across vitreous." in text


def test_wikilink_uses_folder_and_date():
    cfg = DevlogConfig(obsidian_folder="DevLog")
    assert wikilink_for(cfg, DAY) == "![[DevLog/2026-08-13]]"


def test_upsert_daily_region_creates_and_replaces_without_clobber():
    created = upsert_daily_region("", DAY, "![[DevLog/2026-08-13]]")
    assert created.startswith("# 2026-08-13\n")
    assert "%%devlog\n![[DevLog/2026-08-13]]\n%%" in created

    existing = "# 2026-08-13\n\nMorning notes.\n\n%%devlog\n![[DevLog/old]]\n%%\n\nEvening.\n"
    updated = upsert_daily_region(existing, DAY, "![[DevLog/2026-08-13]]")
    assert "Morning notes." in updated
    assert "Evening." in updated
    assert "![[DevLog/2026-08-13]]" in updated
    assert "![[DevLog/old]]" not in updated
    assert updated.count("%%devlog") == 1

    appended = upsert_daily_region("# 2026-08-13\n\nOnly journal.\n", DAY, "![[DevLog/2026-08-13]]")
    assert "Only journal." in appended
    assert appended.count("%%devlog") == 1


def test_strip_daily_region_keeps_other_text():
    text = "# 2026-08-13\n\nKeep me.\n\n%%devlog\n![[DevLog/2026-08-13]]\n%%\n"
    stripped = strip_daily_region(text)
    assert "Keep me." in stripped
    assert "%%devlog" not in stripped
    assert "![[DevLog/2026-08-13]]" not in stripped


def test_try_mirror_post_writes_archive_and_daily(tmp_path: Path):
    cfg = _cfg(tmp_path)
    out = try_mirror_post(cfg, DAY, POST)

    assert out["status"] == "written"
    archive = archive_path(cfg, DAY)
    daily = daily_path(cfg, DAY)
    assert archive.is_file()
    assert daily.is_file()
    body = archive.read_text(encoding="utf-8")
    assert "date: 2026-08-13" in body
    assert "Today I logged 328 active min" in body
    daily_text = daily.read_text(encoding="utf-8")
    assert "%%devlog" in daily_text
    assert "![[DevLog/2026-08-13]]" in daily_text


def test_try_mirror_post_disabled_when_vault_empty(tmp_path: Path):
    cfg = _cfg(tmp_path, obsidian_vault="")
    out = try_mirror_post(cfg, DAY, POST)
    assert out["status"] == "disabled"
    assert not list((tmp_path / "vault").rglob("*.md")) if (tmp_path / "vault").exists() else True


def test_try_mirror_post_missing_vault_does_not_create_it(tmp_path: Path):
    missing = tmp_path / "no-such-vault"
    cfg = _cfg(tmp_path, obsidian_vault=str(missing).replace("\\", "/"))
    # _cfg mkdir'd via vault= default; override path that does not exist
    cfg.obsidian_vault = str(missing).replace("\\", "/")
    out = try_mirror_post(cfg, DAY, POST)
    assert out["status"] == "vault_missing"
    assert not missing.exists()


def test_try_mirror_post_force_overwrites_archive(tmp_path: Path):
    cfg = _cfg(tmp_path)
    try_mirror_post(cfg, DAY, POST)
    try_mirror_post(cfg, DAY, "# 2026-08-13\n\nRewritten body.\n")
    text = archive_path(cfg, DAY).read_text(encoding="utf-8")
    assert "Rewritten body." in text
    assert "328 active min" not in text
    daily = daily_path(cfg, DAY).read_text(encoding="utf-8")
    assert daily.count("%%devlog") == 1


def test_remove_mirrored_post_deletes_archive_and_strips_daily(tmp_path: Path):
    cfg = _cfg(tmp_path)
    try_mirror_post(cfg, DAY, POST)
    daily = daily_path(cfg, DAY)
    daily.write_text(
        "# 2026-08-13\n\nKeep me.\n\n%%devlog\n![[DevLog/2026-08-13]]\n%%\n",
        encoding="utf-8",
    )
    out = remove_mirrored_post(cfg, DAY)
    assert out["status"] == "removed"
    assert not archive_path(cfg, DAY).exists()
    leftover = daily.read_text(encoding="utf-8")
    assert "Keep me." in leftover
    assert "%%devlog" not in leftover


def test_should_remove_from_vault_defaults_to_preserve():
    cfg = DevlogConfig(obsidian_on_delete="preserve")
    assert should_remove_from_vault(cfg, also_obsidian=False) is False
    assert should_remove_from_vault(cfg, also_obsidian=True) is True
    cfg.obsidian_on_delete = "remove"
    assert should_remove_from_vault(cfg, also_obsidian=False) is True


def test_planned_paths_when_enabled(tmp_path: Path):
    cfg = _cfg(tmp_path)
    paths = planned_paths(cfg, DAY)
    assert paths["status"] == "enabled"
    assert paths["archive"].endswith("DevLog/2026-08-13.md") or paths["archive"].endswith(
        "DevLog\\2026-08-13.md"
    )
    assert "Daily" in paths["daily"]


def test_backfill_mirrors_existing_posts(tmp_path: Path):
    cfg = _cfg(tmp_path)
    repo = tmp_path / "repo"
    posts = repo / "posts"
    posts.mkdir(parents=True)
    (posts / "2026-08-13.md").write_text(POST, encoding="utf-8")
    (posts / "2026-08-12.md").write_text("# 2026-08-12\n\nEarlier day.\n", encoding="utf-8")

    out = backfill_posts(cfg, posts)
    assert out["status"] == "written"
    assert out["count"] == 2
    assert archive_path(cfg, DAY).is_file()
    assert archive_path(cfg, date(2026, 8, 12)).is_file()
    assert "Earlier day." in archive_path(cfg, date(2026, 8, 12)).read_text(encoding="utf-8")


def test_backfill_one_date(tmp_path: Path):
    cfg = _cfg(tmp_path)
    posts = tmp_path / "repo" / "posts"
    posts.mkdir(parents=True)
    (posts / "2026-08-13.md").write_text(POST, encoding="utf-8")
    (posts / "2026-08-12.md").write_text("# 2026-08-12\n\nEarlier day.\n", encoding="utf-8")

    out = backfill_posts(cfg, posts, target=DAY)
    assert out["count"] == 1
    assert archive_path(cfg, DAY).is_file()
    assert not archive_path(cfg, date(2026, 8, 12)).exists()


def test_backfill_dry_run_writes_nothing(tmp_path: Path):
    cfg = _cfg(tmp_path)
    posts = tmp_path / "repo" / "posts"
    posts.mkdir(parents=True)
    (posts / "2026-08-13.md").write_text(POST, encoding="utf-8")

    out = backfill_posts(cfg, posts, dry_run=True)
    assert out["status"] == "dry_run"
    assert out["count"] == 1
    assert not archive_path(cfg, DAY).exists()


def test_load_config_missing_obsidian_keys_defaults(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        'sources = ["claude_code"]\n'
        'claude_root = "~/.claude"\n'
        'codex_root = "~/.codex"\n'
        'cursor_root = "~/.cursor"\n'
        'repo_path = "C:/tmp/devlog"\n'
        'publish_mode = "manual"\n'
        'schedule_time = "06:30"\n'
        'remote = "origin"\n'
        'branch = "main"\n'
        'model = "claude-sonnet-5"\n'
        "allow_external_api = false\n",
        encoding="utf-8",
    )
    loaded = load_config(path)
    assert loaded is not None
    assert loaded.obsidian_vault == ""
    assert loaded.obsidian_folder == "DevLog"
    assert loaded.obsidian_daily_folder == "Daily"
    assert loaded.obsidian_on_delete == "preserve"


def test_save_and_load_obsidian_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    cfg = DevlogConfig(
        publish_mode="manual",
        repo_path=str(tmp_path).replace("\\", "/"),
        obsidian_vault="C:/Users/shibi/Vault",
        obsidian_folder="DevLog",
        obsidian_daily_folder="Daily",
        obsidian_on_delete="remove",
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded is not None
    assert loaded.obsidian_vault == "C:/Users/shibi/Vault"
    assert loaded.obsidian_on_delete == "remove"


def test_config_rejects_bad_obsidian_on_delete():
    with pytest.raises(ValueError, match="obsidian_on_delete"):
        DevlogConfig(obsidian_on_delete="wipe").validate()


def _publish_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "docs" / "index.html").write_text(
        '<a href="https://github.com/musicofthings/devlog">Open on GitHub →</a>\n',
        encoding="utf-8",
    )
    return repo


def test_publish_writes_vault_when_enabled(tmp_path: Path):
    from devlog.obsidian import archive_path, daily_path
    from devlog.publish import publish_day

    repo = _publish_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "codex"
    cfg = DevlogConfig(
        sources=["codex"],
        codex_root=str(sample),
        repo_path=str(repo).replace("\\", "/"),
        publish_mode="manual",
        obsidian_vault=str(vault).replace("\\", "/"),
    )
    out = publish_day(cfg, date(2026, 7, 20), force=True)
    assert out["status"] == "written"
    assert out["obsidian"]["status"] == "written"
    assert archive_path(cfg, date(2026, 7, 20)).is_file()
    assert daily_path(cfg, date(2026, 7, 20)).is_file()
    assert (repo / "posts" / "2026-07-20.md").exists()


def test_publish_skips_vault_when_disabled(tmp_path: Path):
    from devlog.publish import publish_day

    repo = _publish_repo(tmp_path)
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "codex"
    cfg = DevlogConfig(
        sources=["codex"],
        codex_root=str(sample),
        repo_path=str(repo).replace("\\", "/"),
        publish_mode="manual",
        obsidian_vault="",
    )
    out = publish_day(cfg, date(2026, 7, 20), force=True)
    assert out["status"] == "written"
    assert out["obsidian"]["status"] == "disabled"
    assert not (tmp_path / "vault").exists() or not list((tmp_path / "vault").rglob("*.md"))


def test_publish_missing_vault_still_succeeds(tmp_path: Path):
    from devlog.publish import publish_day

    repo = _publish_repo(tmp_path)
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "codex"
    missing = tmp_path / "no-vault"
    cfg = DevlogConfig(
        sources=["codex"],
        codex_root=str(sample),
        repo_path=str(repo).replace("\\", "/"),
        publish_mode="manual",
        obsidian_vault=str(missing).replace("\\", "/"),
    )
    out = publish_day(cfg, date(2026, 7, 20), force=True)
    assert out["status"] == "written"
    assert out["obsidian"]["status"] == "vault_missing"
    assert (repo / "posts" / "2026-07-20.md").exists()
    assert not missing.exists()


def test_publish_dry_run_includes_vault_paths(tmp_path: Path):
    from devlog.publish import publish_day

    repo = _publish_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "codex"
    cfg = DevlogConfig(
        sources=["codex"],
        codex_root=str(sample),
        repo_path=str(repo).replace("\\", "/"),
        publish_mode="manual",
        obsidian_vault=str(vault).replace("\\", "/"),
    )
    out = publish_day(cfg, date(2026, 7, 20), dry_run=True)
    assert out["status"] == "dry_run"
    assert out["obsidian"]["status"] == "enabled"
    assert "DevLog" in out["obsidian"]["archive"]
    assert not archive_path(cfg, date(2026, 7, 20)).exists()


def test_hide_does_not_touch_vault(tmp_path: Path):
    from devlog.hide_cmd import hide_day
    from devlog.obsidian import archive_path, try_mirror_post
    from devlog.site import rebuild_site, write_post_markdown

    repo = _publish_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    day = date(2026, 7, 20)
    write_post_markdown(repo / "posts", day, "Built the parser.")
    rebuild_site(repo)
    cfg = DevlogConfig(
        repo_path=str(repo).replace("\\", "/"),
        remote="origin",
        branch="main",
        obsidian_vault=str(vault).replace("\\", "/"),
    )
    try_mirror_post(cfg, day, "# 2026-07-20\n\nBuilt the parser.\n")
    archive = archive_path(cfg, day)
    before = archive.read_text(encoding="utf-8")

    def fake_git(cmd: list[str], cwd: Path):
        from subprocess import CompletedProcess

        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="A .devlog-hidden.json\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    hide_day(cfg, day, git_run=fake_git)
    assert archive.read_text(encoding="utf-8") == before


def test_delete_preserve_keeps_vault_notes(tmp_path: Path):
    from devlog.delete_cmd import delete_day
    from devlog.obsidian import archive_path, try_mirror_post
    from devlog.site import rebuild_site, write_post_markdown

    repo = _publish_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    day = date(2026, 7, 20)
    write_post_markdown(repo / "posts", day, "Built the parser.")
    rebuild_site(repo)
    cfg = DevlogConfig(
        repo_path=str(repo).replace("\\", "/"),
        remote="origin",
        branch="main",
        obsidian_vault=str(vault).replace("\\", "/"),
        obsidian_on_delete="preserve",
    )
    try_mirror_post(cfg, day, "# 2026-07-20\n\nBuilt the parser.\n")

    def fake_git(cmd: list[str], cwd: Path):
        from subprocess import CompletedProcess

        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="D posts/2026-07-20.md\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    out = delete_day(cfg, day, git_run=fake_git)
    assert out["status"] == "deleted"
    assert out["obsidian"]["status"] == "preserved"
    assert archive_path(cfg, day).is_file()


def test_delete_also_obsidian_removes_vault_notes(tmp_path: Path):
    from devlog.delete_cmd import delete_day
    from devlog.obsidian import archive_path, try_mirror_post
    from devlog.site import rebuild_site, write_post_markdown

    repo = _publish_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    day = date(2026, 7, 20)
    write_post_markdown(repo / "posts", day, "Built the parser.")
    rebuild_site(repo)
    cfg = DevlogConfig(
        repo_path=str(repo).replace("\\", "/"),
        remote="origin",
        branch="main",
        obsidian_vault=str(vault).replace("\\", "/"),
        obsidian_on_delete="preserve",
    )
    try_mirror_post(cfg, day, "# 2026-07-20\n\nBuilt the parser.\n")

    def fake_git(cmd: list[str], cwd: Path):
        from subprocess import CompletedProcess

        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="D posts/2026-07-20.md\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    out = delete_day(cfg, day, git_run=fake_git, also_obsidian=True)
    assert out["status"] == "deleted"
    assert out["obsidian"]["status"] == "removed"
    assert not archive_path(cfg, day).exists()


def test_delete_on_delete_remove_config_removes_vault(tmp_path: Path):
    from devlog.delete_cmd import delete_day
    from devlog.obsidian import archive_path, try_mirror_post
    from devlog.site import rebuild_site, write_post_markdown

    repo = _publish_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    day = date(2026, 7, 20)
    write_post_markdown(repo / "posts", day, "Built the parser.")
    rebuild_site(repo)
    cfg = DevlogConfig(
        repo_path=str(repo).replace("\\", "/"),
        remote="origin",
        branch="main",
        obsidian_vault=str(vault).replace("\\", "/"),
        obsidian_on_delete="remove",
    )
    try_mirror_post(cfg, day, "# 2026-07-20\n\nBuilt the parser.\n")

    def fake_git(cmd: list[str], cwd: Path):
        from subprocess import CompletedProcess

        if cmd[:2] == ["git", "status"]:
            return CompletedProcess(cmd, 0, stdout="D posts/2026-07-20.md\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    out = delete_day(cfg, day, git_run=fake_git)
    assert out["obsidian"]["status"] == "removed"
    assert not archive_path(cfg, day).exists()


def test_cmd_obsidian_backfill(tmp_path: Path, capsys):
    from devlog.cli import main
    from devlog.obsidian import archive_path

    repo = _publish_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    posts = repo / "posts"
    posts.mkdir()
    (posts / "2026-08-13.md").write_text(POST, encoding="utf-8")
    cfg_path = tmp_path / "config.toml"
    save_config(
        DevlogConfig(
            repo_path=str(repo).replace("\\", "/"),
            publish_mode="manual",
            obsidian_vault=str(vault).replace("\\", "/"),
        ),
        cfg_path,
    )

    code = main(["obsidian", "--backfill", "--config", str(cfg_path)])
    assert code == 0
    cfg = load_config(cfg_path)
    assert cfg is not None
    assert archive_path(cfg, DAY).is_file()
    assert "obsidian written" in capsys.readouterr().out


def test_cmd_obsidian_dry_run_backfill_writes_nothing(tmp_path: Path):
    from devlog.cli import main
    from devlog.obsidian import archive_path

    repo = _publish_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    posts = repo / "posts"
    posts.mkdir()
    (posts / "2026-08-13.md").write_text(POST, encoding="utf-8")
    cfg_path = tmp_path / "config.toml"
    cfg = DevlogConfig(
        repo_path=str(repo).replace("\\", "/"),
        publish_mode="manual",
        obsidian_vault=str(vault).replace("\\", "/"),
    )
    save_config(cfg, cfg_path)

    code = main(["obsidian", "--backfill", "--dry-run", "--config", str(cfg_path)])
    assert code == 0
    assert not archive_path(cfg, DAY).exists()


def _obsidian_json(tmp_path: Path, vaults: dict) -> Path:
    path = tmp_path / "appdata" / "obsidian" / "obsidian.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"vaults": vaults}), encoding="utf-8")
    return path


def test_detect_prefers_open_vault(tmp_path: Path):
    open_vault = tmp_path / "open-vault"
    other = tmp_path / "other-vault"
    open_vault.mkdir()
    other.mkdir()
    app_cfg = _obsidian_json(
        tmp_path,
        {
            "aaa": {"path": str(other), "ts": 99, "open": False},
            "bbb": {"path": str(open_vault), "ts": 1, "open": True},
        },
    )
    assert detect_obsidian_vault(app_config=app_cfg) == open_vault.resolve()


def test_detect_falls_back_to_newest_ts(tmp_path: Path):
    older = tmp_path / "older"
    newer = tmp_path / "newer"
    older.mkdir()
    newer.mkdir()
    app_cfg = _obsidian_json(
        tmp_path,
        {
            "aaa": {"path": str(older), "ts": 10},
            "bbb": {"path": str(newer), "ts": 20},
        },
    )
    assert detect_obsidian_vault(app_config=app_cfg) == newer.resolve()


def test_detect_skips_missing_paths(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    app_cfg = _obsidian_json(
        tmp_path,
        {
            "gone": {"path": str(tmp_path / "missing"), "ts": 99, "open": True},
            "ok": {"path": str(real), "ts": 1},
        },
    )
    assert detect_obsidian_vault(app_config=app_cfg) == real.resolve()


def test_detect_returns_none_when_no_config(tmp_path: Path):
    assert detect_obsidian_vault(app_config=tmp_path / "nope.json") is None


def test_detect_tolerates_malformed_json(tmp_path: Path):
    path = tmp_path / "obsidian.json"
    path.write_text("{not json", encoding="utf-8")
    assert detect_obsidian_vault(app_config=path) is None


def test_create_obsidian_vault_writes_dot_obsidian(tmp_path: Path):
    vault = tmp_path / "Documents" / "DevLog"
    created = create_obsidian_vault(vault)
    assert created == vault
    assert (vault / ".obsidian" / "app.json").is_file()
    assert (vault / ".obsidian" / "daily-notes.json").is_file()
    assert (vault / "DevLog").is_dir()
    assert (vault / "Daily").is_dir()
    daily = (vault / ".obsidian" / "daily-notes.json").read_text(encoding="utf-8")
    assert '"folder": "Daily"' in daily or '"folder":"Daily"' in daily


def test_create_obsidian_vault_is_idempotent(tmp_path: Path):
    vault = tmp_path / "vault"
    create_obsidian_vault(vault)
    (vault / ".obsidian" / "app.json").write_text('{"existing": true}\n', encoding="utf-8")
    create_obsidian_vault(vault)
    assert '"existing"' in (vault / ".obsidian" / "app.json").read_text(encoding="utf-8")


def test_register_obsidian_vault_appends_without_clobber(tmp_path: Path):
    existing = tmp_path / "existing"
    existing.mkdir()
    new_vault = tmp_path / "new"
    new_vault.mkdir()
    app_cfg = _obsidian_json(
        tmp_path, {"aaa": {"path": str(existing), "ts": 1, "open": True}}
    )
    register_obsidian_vault(new_vault, app_config=app_cfg)
    data = json.loads(app_cfg.read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in data["vaults"].values()}
    assert any(Path(p).resolve() == existing.resolve() for p in paths)
    assert any(Path(p).resolve() == new_vault.resolve() for p in paths)
    open_flags = [v.get("open") for v in data["vaults"].values() if v.get("open")]
    assert open_flags  # still has an open vault


def test_ensure_uses_detected_vault(tmp_path: Path):
    vault = tmp_path / "open-vault"
    vault.mkdir()
    app_cfg = _obsidian_json(
        tmp_path, {"bbb": {"path": str(vault), "ts": 1, "open": True}}
    )
    path, source = ensure_obsidian_vault(
        app_config=app_cfg, create_path=tmp_path / "Documents" / "DevLog"
    )
    assert path == vault.resolve()
    assert source == "open"
    assert not (tmp_path / "Documents" / "DevLog").exists()


def test_ensure_creates_default_when_none_detected(tmp_path: Path):
    app_cfg = tmp_path / "appdata" / "obsidian" / "obsidian.json"
    create_at = tmp_path / "Documents" / "DevLog"
    path, source = ensure_obsidian_vault(app_config=app_cfg, create_path=create_at)
    assert source == "created"
    assert path == create_at
    assert (create_at / ".obsidian" / "app.json").is_file()


def test_init_defaults_detects_or_creates_vault(tmp_path: Path, monkeypatch):
    from devlog.init_cmd import cmd_init

    monkeypatch.setattr("devlog.init_cmd.unregister_windows_task", lambda: None)
    monkeypatch.setattr(
        "devlog.init_cmd.write_publish_now_shortcut",
        lambda cfg, **kwargs: tmp_path / "Publish Devlog Now.cmd",
    )
    app_cfg = tmp_path / "obsidian.json"
    create_at = tmp_path / "Documents" / "DevLog"
    monkeypatch.setattr("devlog.obsidian.obsidian_app_config_path", lambda: app_cfg)
    monkeypatch.setattr("devlog.obsidian.default_new_vault_path", lambda: create_at)

    cfg_path = tmp_path / "devlog" / "config.toml"
    assert cmd_init(["--defaults", "--no-schedule", "--config", str(cfg_path)]) == 0
    loaded = load_config(cfg_path)
    assert loaded is not None
    assert loaded.obsidian_vault == str(create_at).replace("\\", "/")
    assert loaded.obsidian_folder == "DevLog"
    assert loaded.obsidian_on_delete == "preserve"
    assert (create_at / ".obsidian").is_dir()


def test_init_defaults_uses_open_vault(tmp_path: Path, monkeypatch):
    from devlog.init_cmd import cmd_init

    monkeypatch.setattr("devlog.init_cmd.unregister_windows_task", lambda: None)
    monkeypatch.setattr(
        "devlog.init_cmd.write_publish_now_shortcut",
        lambda cfg, **kwargs: tmp_path / "Publish Devlog Now.cmd",
    )
    vault = tmp_path / "my-vault"
    vault.mkdir()
    app_cfg = _obsidian_json(
        tmp_path, {"id": {"path": str(vault), "ts": 1, "open": True}}
    )
    monkeypatch.setattr("devlog.obsidian.obsidian_app_config_path", lambda: app_cfg)
    monkeypatch.setattr(
        "devlog.obsidian.default_new_vault_path", lambda: tmp_path / "Documents" / "DevLog"
    )

    cfg_path = tmp_path / "devlog" / "config.toml"
    assert cmd_init(["--defaults", "--no-schedule", "--config", str(cfg_path)]) == 0
    loaded = load_config(cfg_path)
    assert loaded is not None
    assert Path(loaded.obsidian_vault).resolve() == vault.resolve()
    assert not (tmp_path / "Documents" / "DevLog").exists()
