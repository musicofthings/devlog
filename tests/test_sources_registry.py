from pathlib import Path

import pytest

from devlog.sources.base import REGISTRY, get_sources

EXPECTED = [
    "claude_code",
    "codex",
    "cursor",
    "grok",
    "copilot",
    "opencode",
    "warp",
    "vitreous",
    "antigravity",
]


def test_all_sources_registered():
    import devlog.sources  # noqa: F401

    for name in EXPECTED:
        assert name in REGISTRY


def test_empty_root_returns_empty(tmp_path: Path):
    import devlog.sources  # noqa: F401

    for name in EXPECTED:
        assert get_sources([name])[0].iter_sessions(tmp_path) == []


def test_unknown_source_raises():
    import devlog.sources  # noqa: F401

    with pytest.raises(KeyError) as exc:
        get_sources(["nope"])
    msg = str(exc.value).lower()
    assert "claude_code" in msg or "known" in msg
