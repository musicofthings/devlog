"""Windows Task Scheduler helpers for nightly publish."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from devlog.config import DevlogConfig

TASK_NAME = "DailyDevLogPublish"


def _python_exe() -> str:
    return str(Path(sys.executable).resolve())


def register_windows_task(cfg: DevlogConfig) -> str:
    """Register or replace a daily schtasks job. Returns task name."""
    if sys.platform != "win32":
        raise RuntimeError("Task Scheduler registration is only supported on Windows")
    if not shutil.which("schtasks"):
        raise RuntimeError("schtasks not found on PATH")

    # HH:MM → HH:MM:00
    time_part = cfg.schedule_time.strip()
    if len(time_part) == 5:
        time_part = f"{time_part}:00"

    # Run: python -m devlog publish --date yesterday
    # Working directory = repo_path so git operations land correctly.
    tr = (
        f'"{_python_exe()}" -m devlog publish --date yesterday'
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
        time_part[:5],
        "/TR",
        tr,
        "/RL",
        "LIMITED",
    ]
    # schtasks doesn't have a clean "start in" flag on all Windows versions;
    # wrap via cmd /c cd /d repo && python ...
    repo = str(Path(cfg.repo_path).expanduser())
    wrapped = f'cmd /c "cd /d {repo} && {_python_exe()} -m devlog publish --date yesterday"'
    cmd[cmd.index(tr)] = wrapped

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
