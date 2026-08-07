import sys

import pytest

from devlog.config import DevlogConfig
from devlog.scheduler import (
    _verify_python_can_import_devlog,
    build_wrapper_script,
    try_enable_task_history,
)


def test_try_enable_task_history_noop_off_windows():
    if sys.platform == "win32":
        pytest.skip("only exercises the non-Windows short-circuit")
    assert try_enable_task_history() is False


def test_verify_python_can_import_devlog_succeeds_for_current_python():
    _verify_python_can_import_devlog(sys.executable)


def test_verify_python_can_import_devlog_raises_for_missing_python(tmp_path):
    missing = tmp_path / "nonexistent-python.exe"
    with pytest.raises(RuntimeError, match="Cannot run"):
        _verify_python_can_import_devlog(str(missing))


def test_wrapper_script_rejects_cmd_metacharacters_in_repo_path():
    cfg = DevlogConfig(repo_path="C:/Users/dev/notes & todo", publish_mode="manual")
    with pytest.raises(ValueError):
        build_wrapper_script(cfg, python_exe="python")


def test_wrapper_script_quotes_paths_with_spaces():
    cfg = DevlogConfig(repo_path="C:/Users/First Last/Projects/devlog", publish_mode="manual")
    script = build_wrapper_script(cfg, python_exe=r"C:\Program Files\Python312\python.exe")
    assert 'cd /d "C:\\Users\\First Last\\Projects\\devlog"' in script
    assert '"C:\\Program Files\\Python312\\python.exe" -m devlog publish' in script


def test_wrapper_script_logs_output():
    cfg = DevlogConfig(publish_mode="manual")
    script = build_wrapper_script(cfg, python_exe="python")
    assert ">>" in script
    assert "publish.log" in script
    assert "2>&1" in script


def test_wrapper_script_uses_custom_config_path(tmp_path):
    cfg = DevlogConfig(repo_path=str(tmp_path), publish_mode="manual")
    config_path = tmp_path / "custom config.toml"

    script = build_wrapper_script(cfg, python_exe="python", config_path=config_path)

    assert f'--config "{config_path.resolve()}"' in script
