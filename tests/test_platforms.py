"""What `devlog init` and `devlog doctor` do on Windows, macOS, and Linux."""

from __future__ import annotations

import plistlib
import subprocess
import sys

import pytest

from devlog import doctor, scheduler
from devlog.config import DevlogConfig, default_warp_root
from devlog.doctor import FAIL, OK, WARN
from devlog.init_cmd import cmd_init

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="uses os.getuid / file modes")


def _done(code: int = 0, out: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, out, "")


@pytest.mark.parametrize(("raw", "host", "name"), [
    ("win32", "windows", "Windows Task Scheduler"),
    ("darwin", "macos", "launchd"),
    ("linux", "linux", "cron"),
    ("freebsd14", "linux", "cron"),
])
def test_host_platform(raw, host, name):
    assert scheduler.host_platform(raw) == host
    assert scheduler.scheduler_name(raw) == name


# ------------------------------------------------------------------ macOS launchd


def test_launchd_plist_runs_yesterdays_publish_at_schedule_time(tmp_path):
    cfg = DevlogConfig(repo_path=str(tmp_path / "my site"), schedule_time="18:05")
    plist = plistlib.loads(scheduler.build_launchd_plist(
        cfg, python_exe="/opt/py/bin/python", config_path=tmp_path / "c.toml",
        log_path=tmp_path / "publish.log", path_env="/opt/homebrew/bin:/usr/bin:/bin"))
    assert plist["Label"] == scheduler.LAUNCHD_LABEL
    assert plist["ProgramArguments"] == [
        "/opt/py/bin/python", "-m", "devlog", "publish", "--date", "yesterday",
        "--config", str((tmp_path / "c.toml").resolve())]
    assert plist["WorkingDirectory"] == str(tmp_path / "my site")
    assert plist["StartCalendarInterval"] == {"Hour": 18, "Minute": 5}
    assert plist["StandardOutPath"] == plist["StandardErrorPath"] == str(tmp_path / "publish.log")
    assert plist["EnvironmentVariables"] == {"PATH": "/opt/homebrew/bin:/usr/bin:/bin"}


@posix_only
def test_register_launchd_agent_writes_and_reloads(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    calls: list[list[str]] = []

    def run(cmd, **_):
        calls.append(cmd)
        return _done()

    cfg = DevlogConfig(repo_path=str(tmp_path), schedule_time="06:30")
    path = scheduler.register_launchd_agent(cfg, run=run, agents_dir=tmp_path / "agents",
                                            python_exe=sys.executable)
    assert path == tmp_path / "agents" / "dev.devlog.publish.plist"
    assert plistlib.loads(path.read_bytes())["StartCalendarInterval"] == {"Hour": 6, "Minute": 30}
    assert [c[:2] for c in calls] == [["launchctl", "bootout"], ["launchctl", "bootstrap"]]
    assert calls[1][2].startswith("gui/") and calls[1][3] == str(path)


@posix_only
def test_register_launchd_agent_keeps_a_hand_added_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    agents = tmp_path / "agents"
    cfg = DevlogConfig(repo_path=str(tmp_path))
    path = scheduler.register_launchd_agent(cfg, run=lambda *a, **k: _done(), agents_dir=agents,
                                            python_exe=sys.executable)
    data = plistlib.loads(path.read_bytes())
    data["EnvironmentVariables"]["ANTHROPIC_API_KEY"] = "sk-ant-kept"
    path.write_bytes(plistlib.dumps(data))

    scheduler.register_launchd_agent(cfg, run=lambda *a, **k: _done(), agents_dir=agents,
                                     python_exe=sys.executable)
    env = plistlib.loads(path.read_bytes())["EnvironmentVariables"]
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-kept"
    assert path.stat().st_mode & 0o077 == 0  # holds a secret: owner-only


@posix_only
def test_register_launchd_agent_reports_bootstrap_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    def run(cmd, **_):
        if cmd[1] == "bootstrap":
            return _done(5, "Bootstrap failed: 5: Input/output error")
        return _done()

    with pytest.raises(RuntimeError, match="Bootstrap failed"):
        scheduler.register_launchd_agent(DevlogConfig(repo_path=str(tmp_path)), run=run,
                                         agents_dir=tmp_path, python_exe=sys.executable)


@posix_only
def test_unregister_launchd_agent_unloads_and_removes(tmp_path):
    plist = tmp_path / "dev.devlog.publish.plist"
    plist.write_bytes(b"x")
    calls: list[list[str]] = []

    def run(cmd, **_):
        calls.append(cmd)
        return _done()

    scheduler.unregister_launchd_agent(run=run, agents_dir=tmp_path)
    assert not plist.exists() and calls[0][:2] == ["launchctl", "bootout"]
    calls.clear()
    scheduler.unregister_launchd_agent(run=run, agents_dir=tmp_path)
    assert calls == []  # nothing registered: nothing to do


@posix_only
def test_publish_now_command_is_an_executable_shell_script(tmp_path):
    cfg = DevlogConfig(repo_path=str(tmp_path / "Dev Log's site"))
    path = scheduler.write_publish_now_command(
        cfg, python_exe=sys.executable, config_path=tmp_path / "c.toml", desktop_dir=tmp_path)
    assert path.name == "Publish Devlog Now.command"
    assert path.stat().st_mode & 0o111
    text = path.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/bash\n")
    assert "Dev Log'\"'\"'s site" in text  # shell-quoted
    assert "-m devlog publish --verbose --config" in text and "read -n 1" in text


def test_cron_line_for_linux(tmp_path):
    cfg = DevlogConfig(repo_path=str(tmp_path / "site"), schedule_time="18:00")
    line = scheduler.cron_line(cfg, python_exe="/usr/bin/python3")
    assert line == (f"0 18 * * * cd {tmp_path / 'site'} && /usr/bin/python3 -m devlog "
                    "publish --date yesterday >> $HOME/devlog-publish.log 2>&1")


# ------------------------------------------------------------------ init per platform


@pytest.fixture
def recorder(monkeypatch, tmp_path):
    monkeypatch.setattr("devlog.obsidian.obsidian_app_config_path", lambda: tmp_path / "none.json")
    monkeypatch.setattr("devlog.obsidian.default_new_vault_path", lambda: tmp_path / "vault")
    called: list[str] = []
    for name in ("register_windows_task", "register_launchd_agent", "unregister_windows_task",
                 "unregister_launchd_agent", "write_publish_now_shortcut",
                 "write_publish_now_command"):
        monkeypatch.setattr(f"devlog.init_cmd.{name}",
                            lambda *a, _n=name, **k: called.append(_n) or tmp_path / _n)
    monkeypatch.setattr("devlog.init_cmd.try_enable_task_history", lambda: True)
    return called


@pytest.mark.parametrize(("host", "expected"), [
    ("windows", ["write_publish_now_shortcut", "register_windows_task"]),
    ("macos", ["write_publish_now_command", "register_launchd_agent"]),
    ("linux", []),  # no shortcut; the cron line is printed, nothing registered
])
def test_init_schedule_uses_only_this_platforms_tools(host, expected, recorder, tmp_path,
                                                     monkeypatch, capsys):
    monkeypatch.setattr("devlog.init_cmd.host_platform", lambda: host)
    assert cmd_init(["--defaults", "--schedule", "--config", str(tmp_path / "c.toml")]) == 0
    assert recorder == expected
    out = capsys.readouterr().out
    assert ("crontab -e" in out) == (host == "linux")
    assert ("Task Scheduler" in out) == (host == "windows")
    assert ("launchd" in out) == (host == "macos")


@pytest.mark.parametrize(("host", "expected"), [
    ("windows", ["write_publish_now_shortcut", "unregister_windows_task"]),
    ("macos", ["write_publish_now_command", "unregister_launchd_agent"]),
    ("linux", []),
])
def test_init_no_schedule_removes_only_this_platforms_job(host, expected, recorder, tmp_path,
                                                         monkeypatch):
    monkeypatch.setattr("devlog.init_cmd.host_platform", lambda: host)
    assert cmd_init(["--defaults", "--no-schedule", "--config", str(tmp_path / "c.toml")]) == 0
    assert recorder == expected


@pytest.mark.parametrize(("host", "raw", "question"), [
    ("macos", "darwin", "Publish nightly at 06:30 with launchd? [y/N]: "),
    ("windows", "win32", "Publish nightly at 06:30 with Windows Task Scheduler? [y/N]: "),
])
def test_interactive_init_asks_about_this_platforms_scheduler(host, raw, question, recorder,
                                                              tmp_path, monkeypatch):
    monkeypatch.setattr("devlog.init_cmd.host_platform", lambda: host)
    monkeypatch.setattr("devlog.init_cmd.scheduler_name", lambda: scheduler.scheduler_name(raw))
    monkeypatch.setattr("devlog.init_cmd.detect_obsidian_vault", lambda: None)
    asked: list[str] = []

    def fake_input(prompt: str) -> str:
        asked.append(prompt)
        if prompt.startswith("obsidian_vault"):
            return " "  # blank the proposed vault: skip it
        return "y" if prompt.startswith("Publish nightly") else ""

    monkeypatch.setattr("builtins.input", fake_input)
    assert cmd_init(["--config", str(tmp_path / "c.toml")]) == 0
    assert asked[-1] == question
    assert recorder[-1] == ("register_launchd_agent" if host == "macos" else
                            "register_windows_task")


def test_interactive_init_on_linux_prints_cron_instead_of_asking(recorder, tmp_path,
                                                                 monkeypatch, capsys):
    monkeypatch.setattr("devlog.init_cmd.host_platform", lambda: "linux")
    monkeypatch.setattr("devlog.init_cmd.detect_obsidian_vault", lambda: None)
    asked: list[str] = []
    monkeypatch.setattr("builtins.input",
                        lambda p: asked.append(p) or (" " if p.startswith("obsidian_vault")
                                                      else ""))
    assert cmd_init(["--config", str(tmp_path / "c.toml")]) == 0
    assert not any("nightly" in q or "Scheduler" in q for q in asked)
    assert "crontab -e" in capsys.readouterr().out
    assert recorder == []


# ------------------------------------------------------------------ doctor per platform


def _cfg(publish_mode: str = "auto") -> DevlogConfig:
    return DevlogConfig(publish_mode=publish_mode)


@posix_only
def test_doctor_macos_schedule(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    def loaded(cmd, cwd):
        return _done(0)

    check = doctor._schedule_check(_cfg(), loaded, "darwin")
    assert check.level == FAIL and "devlog init --schedule" in check.hint
    assert doctor._schedule_check(_cfg("manual"), loaded, "darwin").level == WARN

    plist = scheduler.launchd_plist_path()
    plist.parent.mkdir(parents=True)
    plist.write_bytes(scheduler.build_launchd_plist(_cfg(), python_exe=sys.executable))
    assert doctor._schedule_check(_cfg(), loaded, "darwin").level == OK
    not_loaded = doctor._schedule_check(_cfg(), lambda c, w: _done(113), "darwin")
    assert "isn't loaded" in not_loaded.message

    plist.write_bytes(scheduler.build_launchd_plist(_cfg(), python_exe="/gone/python"))
    check = doctor._schedule_check(_cfg(), loaded, "darwin")
    assert check.level == FAIL and "no longer exists" in check.message


def test_doctor_linux_looks_for_a_cron_entry():
    def with_cron(cmd, cwd):
        return _done(0, "0 18 * * * cd ~/site && devlog publish\n")

    assert doctor._schedule_check(_cfg(), with_cron, "linux").level == OK
    check = doctor._schedule_check(_cfg(), lambda c, w: _done(1), "linux")
    assert check.level == WARN and "crontab -e" in check.hint


def test_doctor_windows_schedule():
    assert doctor._schedule_check(_cfg(), lambda c, w: _done(0), "win32").level == OK
    assert doctor._schedule_check(_cfg(), lambda c, w: _done(1), "win32").level == FAIL


# ------------------------------------------------------------------ sources


def test_warp_root_on_macos_finds_the_group_container(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    fallback = str(tmp_path / ".local" / "share" / "warp" / "Warp").replace("\\", "/")
    assert default_warp_root("darwin") == fallback  # Warp not installed

    app = (tmp_path / "Library" / "Group Containers" / "2BBY89MBSN.dev.warp" / "Library"
           / "Application Support" / "dev.warp.Warp-Stable")
    app.mkdir(parents=True)
    (app / "warp.sqlite").write_bytes(b"")
    assert default_warp_root("darwin") == str(app).replace("\\", "/")
    assert default_warp_root("linux") == fallback


@pytest.mark.parametrize(("host", "expected"), [
    ("windows", ["register_windows_task"]),
    ("macos", ["register_launchd_agent"]),
    ("linux", []),
])
def test_schedule_only_reregisters_without_touching_the_config(host, expected, recorder,
                                                              tmp_path, monkeypatch):
    from devlog.config import save_config

    monkeypatch.setattr("devlog.init_cmd.host_platform", lambda: host)
    cfg_path = tmp_path / "c.toml"
    assert cmd_init(["--schedule-only", "--config", str(cfg_path)]) == 2  # no config yet

    save_config(DevlogConfig(publish_mode="review", schedule_time="18:00"), cfg_path)
    before = cfg_path.read_bytes()
    monkeypatch.setattr("builtins.input", lambda p: pytest.fail(f"asked: {p}"))
    assert cmd_init(["--schedule-only", "--config", str(cfg_path)]) == 0
    assert recorder == expected
    assert cfg_path.read_bytes() == before


def test_dash_skips_the_vault_and_blank_keeps_the_default(recorder, tmp_path, monkeypatch):
    from devlog.config import load_config

    monkeypatch.setattr("devlog.init_cmd.host_platform", lambda: "linux")
    monkeypatch.setattr("devlog.init_cmd.detect_obsidian_vault", lambda: tmp_path / "Personal")
    monkeypatch.setattr("builtins.input",
                        lambda p: "-" if p.startswith("obsidian_vault") else "")
    assert cmd_init(["--config", str(tmp_path / "a.toml")]) == 0
    assert load_config(tmp_path / "a.toml").obsidian_vault == ""

    (tmp_path / "Personal" / ".obsidian").mkdir(parents=True)
    monkeypatch.setattr("builtins.input",
                        lambda p: "-" if p.startswith("obsidian_daily_folder") else "")
    assert cmd_init(["--config", str(tmp_path / "b.toml")]) == 0
    cfg = load_config(tmp_path / "b.toml")
    assert cfg.obsidian_vault == str(tmp_path / "Personal").replace("\\", "/")
    assert cfg.obsidian_daily_folder == ""  # daily notes at the vault root
