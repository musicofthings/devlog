"""Structural evals for AI-assistant slash commands / skills.

Live discovery differs by host (Claude commands, Cursor/Grok skills, Codex
``.agents/skills``). These tests keep the four command surfaces in sync and
assert the behavioral contracts Claude already validated manually.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    "devlog-init",
    "devlog-publish",
    "devlog-delete",
    "devlog-hide",
    "devlog-unhide",
    "devlog-status",
)

SURFACES = {
    "claude": ROOT / ".claude" / "commands",
    "cursor": ROOT / ".cursor" / "skills",
    "grok": ROOT / ".grok" / "skills",
    "codex_skills": ROOT / ".agents" / "skills",
    "codex_prompts_legacy": ROOT / ".codex" / "prompts",
}


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}, text
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta: dict[str, str] = {}
    for line in normalized[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip("\"'")
    return meta, normalized[end + 5 :].strip()


def _command_path(surface: str, name: str) -> Path:
    if surface in {"claude", "codex_prompts_legacy"}:
        return SURFACES[surface] / f"{name}.md"
    return SURFACES[surface] / name / "SKILL.md"


@pytest.mark.parametrize("name", COMMANDS)
@pytest.mark.parametrize("surface", ["claude", "cursor", "grok", "codex_skills"])
def test_command_file_exists_with_description(surface: str, name: str):
    path = _command_path(surface, name)
    assert path.is_file(), f"missing {path.relative_to(ROOT)}"
    meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    assert meta.get("description"), f"{path} needs frontmatter description"
    assert body, f"{path} needs instruction body"
    if surface in {"cursor", "grok", "codex_skills"}:
        assert meta.get("name") == name, f"{path} name must be {name}"


@pytest.mark.parametrize("name", COMMANDS)
def test_grok_skills_are_user_invocable(name: str):
    path = _command_path("grok", name)
    meta, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
    assert meta.get("user-invocable") == "true"


def test_delete_requires_confirmation_on_all_surfaces():
    needle = re.compile(r"never run the real delete without an explicit yes", re.I)
    dry = re.compile(r"devlog delete --date .+ --dry-run")
    for surface in ("claude", "cursor", "grok", "codex_skills"):
        text = _command_path(surface, "devlog-delete").read_text(encoding="utf-8")
        assert needle.search(text), f"{surface} delete missing confirmation rule"
        assert dry.search(text), f"{surface} delete missing dry-run first step"


def test_hide_and_unhide_require_confirmation_on_all_surfaces():
    for name, verb in (("devlog-hide", "hide"), ("devlog-unhide", "unhide")):
        needle = re.compile(rf"never run the real {verb} without an explicit yes", re.I)
        dry = re.compile(rf"devlog {verb} --date .+ --dry-run")
        for surface in ("claude", "cursor", "grok", "codex_skills"):
            text = _command_path(surface, name).read_text(encoding="utf-8")
            assert needle.search(text), f"{surface} {name} missing confirmation rule"
            assert dry.search(text), f"{surface} {name} missing dry-run first step"


def test_publish_mentions_force_prompt_on_all_surfaces():
    for surface in ("claude", "cursor", "grok", "codex_skills"):
        text = _command_path(surface, "devlog-publish").read_text(encoding="utf-8")
        assert "devlog publish" in text
        assert "--force" in text


def test_status_checks_status_file_and_schedule():
    for surface in ("claude", "cursor", "grok", "codex_skills"):
        text = _command_path(surface, "devlog-status").read_text(encoding="utf-8")
        assert ".devlog-status.json" in text
        assert "devlog publish --dry-run" in text
        assert "DailyDevLogPublish" in text
        assert ".devlog-hidden.json" in text


def test_legacy_codex_prompts_still_present_alongside_agents_skills():
    """Codex CLI >=0.117 removed custom prompts; skills live under .agents/skills."""
    for name in COMMANDS:
        assert _command_path("codex_prompts_legacy", name).is_file()
        assert _command_path("codex_skills", name).is_file()
