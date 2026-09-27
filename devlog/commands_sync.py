"""Generate the assistant slash commands / skills from `commands/*.md`.

One source file per command feeds five surfaces:

    .claude/commands/<name>.md          Claude Code  (description, argument-hint, $ARGUMENTS body)
    .cursor/skills/<name>/SKILL.md      Cursor       (name, skill description, skill body)
    .grok/skills/<name>/SKILL.md        Grok Build   (+ argument-hint, user-invocable)
    .agents/skills/<name>/SKILL.md      Codex CLI    (name, skill description, skill body)
    .codex/prompts/<name>.md            legacy Codex prompt: deprecation stub only

Source format (`commands/<name>.md`)::

    ---
    description: Short text shown in the Claude command list
    skill-description: Longer text with "Use when ..." for skill auto-triggering
    argument-hint: "<YYYY-MM-DD>"          (optional)
    ---
    <!-- claude -->
    Body for Claude Code; may use $ARGUMENTS.
    <!-- skills -->
    Body for skill hosts, which have no $ARGUMENTS.

Without the two markers the same body is used everywhere.

    python -m devlog.commands_sync           # rewrite generated files
    python -m devlog.commands_sync --check   # exit 1 if any file is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "commands"
CLAUDE_MARK = "<!-- claude -->"
SKILLS_MARK = "<!-- skills -->"


def parse_source(text: str) -> tuple[dict[str, str], str, str]:
    """(frontmatter, claude body, skills body)."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise ValueError("command source must start with --- frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unterminated frontmatter")
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    body = text[end + 5 :]
    if CLAUDE_MARK in body and SKILLS_MARK in body:
        claude = body.split(CLAUDE_MARK, 1)[1].split(SKILLS_MARK, 1)[0]
        skills = body.split(SKILLS_MARK, 1)[1]
    else:
        claude = skills = body
    for required in ("description", "skill-description"):
        if not meta.get(required):
            raise ValueError(f"command source is missing `{required}`")
    return meta, claude.strip() + "\n", skills.strip() + "\n"


def _doc(frontmatter: list[str], body: str) -> str:
    return "---\n" + "\n".join(frontmatter) + "\n---\n\n" + body


def render(name: str, source: str) -> dict[Path, str]:
    meta, claude_body, skills_body = parse_source(source)
    hint = [f"argument-hint: {meta['argument-hint']}"] if meta.get("argument-hint") else []
    skill = [f"name: {name}", f"description: {meta['skill-description']}"]
    stub = (
        "---\n"
        f'description: "DEPRECATED — use Codex skill .agents/skills/{name} instead"\n'
        "---\n\n"
        "Custom Codex prompts (`/prompts:…`) were removed in Codex CLI 0.117+.\n"
        f"Use the repo skill at `.agents/skills/{name}/SKILL.md` (or ask Codex\n"
        f"to run the `{name}` skill).\n"
    )
    return {
        ROOT / ".claude" / "commands" / f"{name}.md":
            _doc([f"description: {meta['description']}", *hint], claude_body),
        ROOT / ".cursor" / "skills" / name / "SKILL.md": _doc(skill, skills_body),
        ROOT / ".grok" / "skills" / name / "SKILL.md":
            _doc([*skill, *hint, "user-invocable: true"], skills_body),
        ROOT / ".agents" / "skills" / name / "SKILL.md": _doc(skill, skills_body),
        ROOT / ".codex" / "prompts" / f"{name}.md": stub,
    }


def generated() -> dict[Path, str]:
    out: dict[Path, str] = {}
    for src in sorted(SOURCE_DIR.glob("*.md")):
        out.update(render(src.stem, src.read_text(encoding="utf-8")))
    return out


def stale() -> list[Path]:
    return [
        path for path, text in generated().items()
        if not path.exists() or path.read_text(encoding="utf-8").replace("\r\n", "\n") != text
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="fail if generated files are stale")
    args = parser.parse_args(argv)
    if args.check:
        bad = stale()
        for path in bad:
            print(f"stale: {path.relative_to(ROOT)}")
        if bad:
            print("Run: python -m devlog.commands_sync")
        return 1 if bad else 0
    for path, text in generated().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {len(generated())} file(s) from {SOURCE_DIR.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
