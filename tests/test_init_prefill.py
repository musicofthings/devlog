"""`devlog init` pre-fills answers, and optional tools that aren't installed are skipped."""

from __future__ import annotations

import subprocess

import pytest

from devlog import doctor
from devlog.config import DevlogConfig, load_config, save_config
from devlog.doctor import FAIL, OK, WARN
from devlog.init_cmd import cmd_init
from devlog.literature import zotero_installed

ZOTERO_DEFAULT = DevlogConfig().zotero_url


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An empty home folder; Path.home() reads HOME on POSIX, USERPROFILE on Windows."""
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    for var in ("LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("devlog.init_cmd.host_platform", lambda: "linux")
    monkeypatch.setattr("devlog.init_cmd.detect_obsidian_vault", lambda: None)
    monkeypatch.setattr("devlog.init_cmd.default_new_vault_path", lambda: home / "Vault")
    return home


def _answer(monkeypatch, answers: dict[str, str] | None = None) -> list[str]:
    """Press Enter at every prompt, except those starting with a key in `answers`."""
    asked: list[str] = []

    def fake_input(prompt: str) -> str:
        asked.append(prompt)
        for start, reply in (answers or {}).items():
            if prompt.startswith(start):
                return reply
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    return asked


def test_enter_through_a_rerun_keeps_every_setting(home, tmp_path, monkeypatch):
    vault = home / "Personal"
    (vault / ".obsidian").mkdir(parents=True)
    current = DevlogConfig(
        sources=["claude_code", "warp"], claude_root=str(home / "cl"), warp_root=str(home / "w p"),
        repo_path=str(home / "projects" / "devlog"), publish_mode="review", schedule_time="18:00",
        remote="upstream", branch="trunk", public_detail="summary", allow_external_api=True,
        obsidian_vault=str(vault), obsidian_folder="Log", obsidian_daily_folder="",
        obsidian_on_delete="remove", redact_patterns=[r"MRN\d{6}"], zotero_url="",
    )
    cfg_path = tmp_path / "c.toml"
    save_config(current, cfg_path)

    asked = _answer(monkeypatch)
    assert cmd_init(["--no-schedule", "--config", str(cfg_path)]) == 0
    assert load_config(cfg_path) == current
    # The prompts showed the current values, and only chosen sources' folders were asked.
    assert "sources (comma-separated) [claude_code,warp]: " in asked
    assert f"warp_root [{str(home / 'w p').replace(chr(92), '/')}]: " in asked
    assert not any(q.startswith(("codex_root", "cursor_root")) for q in asked)
    assert "publish_mode (auto|pr|manual|review) [review]: " in asked
    assert "schedule_time (HH:MM local) [18:00]: " in asked


def test_first_run_prefills_only_the_sources_found_here(home, tmp_path, monkeypatch):
    (home / ".claude").mkdir()
    (home / ".codex").mkdir()
    asked = _answer(monkeypatch, {"obsidian_vault": "-"})
    cfg_path = tmp_path / "c.toml"
    assert cmd_init(["--no-schedule", "--config", str(cfg_path)]) == 0
    assert "sources (comma-separated) [claude_code,codex]: " in asked
    assert load_config(cfg_path).sources == ["claude_code", "codex"]
    assert [q.split(" [")[0] for q in asked if q.split(" ")[0].endswith("_root")] == [
        "claude_root", "codex_root"]


def test_first_run_offers_every_source_when_none_are_found(home, tmp_path, monkeypatch):
    asked = _answer(monkeypatch, {"obsidian_vault": "-"})
    assert cmd_init(["--no-schedule", "--config", str(tmp_path / "c.toml")]) == 0
    assert asked[0].startswith("sources (comma-separated) [claude_code,codex,cursor,")


def test_defaults_mode_uses_detected_sources(home, tmp_path, monkeypatch):
    (home / ".cursor").mkdir()
    monkeypatch.setattr("devlog.init_cmd.ensure_obsidian_vault",
                        lambda: (home / "Vault", "created"))
    cfg_path = tmp_path / "c.toml"
    assert cmd_init(["--defaults", "--no-schedule", "--config", str(cfg_path)]) == 0
    assert load_config(cfg_path).sources == ["cursor"]


@pytest.mark.parametrize(("installed", "existing_url", "expected"), [
    (False, None, ""),                       # not installed: lookups off
    (True, None, ZOTERO_DEFAULT),            # installed: keep the default
    (False, "http://nas:23119/rpc", "http://nas:23119/rpc"),  # hand-set: never touched
])
def test_init_turns_zotero_off_only_when_it_is_not_installed(installed, existing_url, expected,
                                                            home, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("devlog.init_cmd.zotero_installed", lambda: installed)
    cfg_path = tmp_path / "c.toml"
    if existing_url:
        save_config(DevlogConfig(zotero_url=existing_url, obsidian_vault=""), cfg_path)
    _answer(monkeypatch, {"obsidian_vault": "-"})
    assert cmd_init(["--no-schedule", "--config", str(cfg_path)]) == 0
    assert load_config(cfg_path).zotero_url == expected
    assert ("Zotero not found" in capsys.readouterr().out) == (expected == "")


def test_an_invalid_existing_config_falls_back_to_detected_values(home, tmp_path, monkeypatch,
                                                                  capsys):
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text('redact_patterns = [""]\n', encoding="utf-8")
    _answer(monkeypatch, {"obsidian_vault": "-"})
    assert cmd_init(["--no-schedule", "--config", str(cfg_path)]) == 0
    assert "Ignoring the existing config" in capsys.readouterr().out
    assert load_config(cfg_path).redact_patterns == []


def test_zotero_installed_detects_the_data_folder(home):
    assert zotero_installed("linux") is False
    (home / "Zotero").mkdir()
    assert zotero_installed("linux") is True


# ------------------------------------------------------------------ doctor: optional pieces


def _checks(tmp_path, *, which=lambda _: None, zotero=False, **cfg):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    path = tmp_path / "doc.toml"
    save_config(DevlogConfig(repo_path=str(repo), **cfg), path)

    def run(cmd, cwd):
        out = "https://github.com/me/site" if cmd[:3] == ["git", "remote", "get-url"] else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    checks = doctor.run_checks(path, run=run, which=which, http_ok=lambda _: False,
                               os_name="linux", zotero_present=lambda: zotero)
    return {c.area: c for c in checks}


def test_doctor_reports_missing_optional_tools_as_off_not_warnings(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    got = _checks(tmp_path, obsidian_vault=str(vault), zotero_url=ZOTERO_DEFAULT)
    assert got["gh"].level == OK and "optional" in got["gh"].message
    assert got["zotero"].level == OK and "not installed" in got["zotero"].message

    no_vault = _checks(tmp_path, obsidian_vault="")
    assert no_vault["vault"].level == OK and "off" in no_vault["vault"].message


def test_doctor_still_flags_optional_tools_a_setting_depends_on(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    got = _checks(tmp_path, zotero=True, publish_mode="pr", obsidian_vault=str(vault),
                  zotero_url=ZOTERO_DEFAULT)
    assert got["gh"].level == FAIL  # pr mode opens pull requests with gh
    assert got["zotero"].level == WARN  # installed but not running
