"""Windows Task Scheduler helpers for nightly publish."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from devlog.config import DevlogConfig

TASK_NAME = "DailyDevLogPublish"
LOG_NAME = "publish.log"


def _python_exe() -> str:
    return str(Path(sys.executable).resolve())


def _app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "devlog"


PUBLISH_NOW_NAME = "Publish Devlog Now.cmd"


def _desktop_dir() -> Path:
    base = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(base) / "Desktop"


_UNSAFE_CMD_CHARS = {'"', "\r", "\n", "&", "|", "<", ">", "^"}


def _cmd_quote(value: str) -> str:
    """Quote a batch-file argument and reject characters that can break quoting."""
    if any(char in value for char in _UNSAFE_CMD_CHARS):
        raise ValueError(f"Unsafe character in scheduled-task path: {value!r}")
    return f'"{value.replace("%", "%%")}"'


def _windows_style_path(value: str) -> str:
    """Render an absolute path using Windows-style backslashes.

    devlog/config.py normalizes stored paths to forward slashes (to avoid
    corrupting TOML's backslash escaping), but this always generates a
    Windows .cmd script -- the output must not depend on which OS happens to
    be running this string-generation logic (e.g. it's unit-tested on Linux
    CI, where pathlib renders paths with forward slashes rather than
    converting them to the Windows separator).
    """
    return str(Path(value).expanduser()).replace("/", "\\")


def build_wrapper_script(
    cfg: DevlogConfig,
    *,
    python_exe: str | None = None,
    config_path: Path | None = None,
) -> str:
    """Content of the .cmd the scheduled task runs.

    A wrapper script sidesteps schtasks /TR quoting problems with paths that
    contain spaces, and gives the run a log file (schtasks discards output).
    """
    repo = _windows_style_path(cfg.repo_path)
    python = python_exe or _python_exe()
    log = str(_app_data_dir() / LOG_NAME)
    config_arg = f" --config {_cmd_quote(str(config_path.resolve()))}" if config_path else ""
    return (
        "@echo off\r\n"
        f"cd /d {_cmd_quote(repo)}\r\n"
        f"{_cmd_quote(python)} -m devlog publish --date yesterday{config_arg} "
        f">> {_cmd_quote(log)} 2>&1\r\n"
    )


def _verify_python_can_import_devlog(python_exe: str) -> None:
    """Confirm python_exe can actually import devlog before scheduling it.

    A stale/broken editable install (e.g. `pip install -e .` run from a
    directory that's since been deleted -- as happened on the maintainer's
    machine after removing a worktree used for other work) makes every
    scheduled run fail silently until someone happens to check the log.
    Catching it now, at registration time, is far cheaper than discovering
    it days later.
    """
    try:
        result = subprocess.run(
            [python_exe, "-c", "import devlog"],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError(f"Cannot run {python_exe!r}: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"{python_exe!r} cannot import devlog -- the scheduled task would "
            "fail every night with this. This usually means the editable "
            "install (`pip install -e .`) points at a directory that no "
            "longer exists. Fix with:\n"
            f'  "{python_exe}" -m pip install -e .\n'
            f"Original error: {(result.stderr or result.stdout).strip()}"
        )


def write_publish_now_shortcut(
    cfg: DevlogConfig,
    *,
    python_exe: str | None = None,
    config_path: Path | None = None,
    desktop_dir: Path | None = None,
) -> Path:
    """Write a double-clickable .cmd that publishes immediately.

    Runs plain `devlog publish` (using its own default date, not a hardcoded
    "yesterday" like the nightly wrapper script), with an interactive window
    that stays open (`pause`) so the user actually sees the result -- unlike
    the scheduled task, this is meant to be watched, not run headless.
    """
    resolved_python = python_exe or _python_exe()
    _verify_python_can_import_devlog(resolved_python)

    repo = _windows_style_path(cfg.repo_path)
    config_arg = f" --config {_cmd_quote(str(config_path.resolve()))}" if config_path else ""
    script = (
        "@echo off\r\n"
        f"cd /d {_cmd_quote(repo)}\r\n"
        f"{_cmd_quote(resolved_python)} -m devlog publish --verbose{config_arg}\r\n"
        "echo.\r\n"
        "pause\r\n"
    )

    target_dir = desktop_dir or _desktop_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / PUBLISH_NOW_NAME
    path.write_text(script, encoding="utf-8")
    return path


def register_windows_task(cfg: DevlogConfig, *, config_path: Path | None = None) -> str:
    """Register or replace a daily schtasks job. Returns task name."""
    if sys.platform != "win32":
        raise RuntimeError("Task Scheduler registration is only supported on Windows")
    if not shutil.which("schtasks"):
        raise RuntimeError("schtasks not found on PATH")

    python_exe = _python_exe()
    _verify_python_can_import_devlog(python_exe)

    app_dir = _app_data_dir()
    app_dir.mkdir(parents=True, exist_ok=True)
    script_path = app_dir / "run_publish.cmd"
    script_path.write_text(
        build_wrapper_script(cfg, python_exe=python_exe, config_path=config_path),
        encoding="utf-8",
    )

    cmd = [
        "schtasks",
        "/Create",
        "/F",
        "/TN",
        TASK_NAME,
        "/SC",
        "DAILY",
        "/ST",
        cfg.schedule_time.strip(),
        "/TR",
        f'"{script_path}"',
        "/RL",
        "LIMITED",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return TASK_NAME


def try_enable_task_history() -> bool:
    """Best-effort: turn on Task Scheduler's operational event log.

    Without this, a scheduled task that silently stops existing (as
    DailyDevLogPublish once did on the maintainer's machine, cause unknown)
    leaves no log trail explaining why. Enabling it requires admin
    elevation, which `devlog init` does not have by default -- this never
    raises, so callers can fall back to printing manual instructions.
    """
    if sys.platform != "win32":
        return False
    result = subprocess.run(
        ["wevtutil", "sl", "Microsoft-Windows-TaskScheduler/Operational", "/e:true"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def unregister_windows_task() -> None:
    if sys.platform != "win32" or not shutil.which("schtasks"):
        return
    subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
        check=False,
        capture_output=True,
        text=True,
    )
