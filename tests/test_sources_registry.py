from pathlib import Path

from devlog.sources.base import REGISTRY, get_sources


def test_all_sources_registered():
    assert "claude_code" in REGISTRY
    assert "codex" in REGISTRY
    assert "cursor" in REGISTRY


def test_empty_root_returns_empty(tmp_path: Path):
    assert get_sources(["codex"])[0].iter_sessions(tmp_path) == []
    assert get_sources(["cursor"])[0].iter_sessions(tmp_path) == []


def test_unknown_source_raises():
    import pytest

    with pytest.raises(KeyError) as exc:
        get_sources(["nope"])
    assert "claude_code" in str(exc.value) or "known" in str(exc.value).lower()
