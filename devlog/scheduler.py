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


def build_wrapper_script(cfg: DevlogConfig, *, python_exe: str | None = None) -> str:
    """Content of the .cmd the scheduled task runs.

    A wrapper script sidesteps schtasks /TR quoting problems with paths that
    contain spaces, and gives the run a log file (schtasks discards output).
    """
    repo = str(Path(cfg.repo_path).expanduser())
    python = python_exe or _python_exe()
    log = str(_app_data_dir() / LOG_NAME)
    return (
        "@echo off\r\n"
        f'cd /d "{repo}"\r\n'
        f'"{python}" -m devlog publish --date yesterday >> "{log}" 2>&1\r\n'
    )


def register_windows_task(cfg: DevlogConfig) -> str:
    """Register or replace a daily schtasks job. Returns task name."""
    if sys.platform != "win32":
        raise RuntimeError("Task Scheduler registration is only supported on Windows")
    if not shutil.which("schtasks"):
        raise RuntimeError("schtasks not found on PATH")

    app_dir = _app_data_dir()
    app_dir.mkdir(parents=True, exist_ok=True)
    script_path = app_dir / "run_publish.cmd"
    script_path.write_text(build_wrapper_script(cfg), encoding="utf-8")

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


def unregister_windows_task() -> None:
    if sys.platform != "win32" or not shutil.which("schtasks"):
        return
    subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
        check=False,
        capture_output=True,
        text=True,
    )
