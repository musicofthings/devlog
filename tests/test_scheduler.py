from devlog.config import DevlogConfig
from devlog.scheduler import build_wrapper_script


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
