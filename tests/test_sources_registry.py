from pathlib import Path
import pytest
from devlog.sources.base import get_sources, REGISTRY

def test_stubs_registered():
    assert "codex" in REGISTRY
    assert "cursor" in REGISTRY

def test_stubs_return_empty(tmp_path: Path):
    assert get_sources(["codex"])[0].iter_sessions(tmp_path) == []
    assert get_sources(["cursor"])[0].iter_sessions(tmp_path) == []

def test_unknown_source_raises():
    with pytest.raises(KeyError) as exc:
        get_sources(["nope"])
    assert "claude_code" in str(exc.value) or "known" in str(exc.value).lower()
