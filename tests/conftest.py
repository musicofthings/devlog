"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_real_scheduling(monkeypatch, tmp_path_factory):
    """`devlog init` must never touch the developer's real scheduler or Desktop.

    Unstubbed, `init --no-schedule` removes the real nightly job (schtasks or
    launchd) and writes a publish-now file to the real Desktop. Tests that
    check this wiring replace these stubs with recorders.
    """
    desktop = tmp_path_factory.mktemp("desktop")
    for name in ("register_windows_task", "register_launchd_agent"):
        monkeypatch.setattr(f"devlog.init_cmd.{name}", lambda *a, **k: "stubbed")
    for name in ("unregister_windows_task", "unregister_launchd_agent"):
        monkeypatch.setattr(f"devlog.init_cmd.{name}", lambda *a, **k: None)
    for name in ("write_publish_now_shortcut", "write_publish_now_command"):
        monkeypatch.setattr(f"devlog.init_cmd.{name}",
                            lambda *a, **k: Path(desktop) / "shortcut")
    # Whether the developer has Zotero installed must not change test results.
    monkeypatch.setattr("devlog.init_cmd.zotero_installed", lambda: False)
