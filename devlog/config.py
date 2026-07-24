"""Load and save ~/.config/devlog/config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

PUBLISH_MODES = ("auto", "pr", "manual")
DEFAULT_SOURCES = ["claude_code", "codex", "cursor"]


def default_config_path() -> Path:
    return Path.home() / ".config" / "devlog" / "config.toml"


def default_repo_path() -> Path:
    # Prefer the installed package's repo checkout when running from source.
    return Path(__file__).resolve().parents[1]


@dataclass
class DevlogConfig:
    sources: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCES))
    claude_root: str = "~/.claude"
    codex_root: str = "~/.codex"
    cursor_root: str = "~/.cursor"
    repo_path: str = field(default_factory=lambda: str(default_repo_path()).replace("\\", "/"))
    publish_mode: str = "auto"
    schedule_time: str = "06:30"
    remote: str = "origin"
    branch: str = "main"

    def root_for(self, source: str) -> Path:
        mapping = {
            "claude_code": self.claude_root,
            "codex": self.codex_root,
            "cursor": self.cursor_root,
        }
        return Path(mapping.get(source, self.claude_root)).expanduser()

    def validate(self) -> None:
        if self.publish_mode not in PUBLISH_MODES:
            raise ValueError(
                f"publish_mode must be one of {', '.join(PUBLISH_MODES)}; got {self.publish_mode!r}"
            )
        if not self.sources:
            raise ValueError("sources must be a non-empty list")


def load_config(path: Path | None = None) -> DevlogConfig | None:
    cfg_path = path or default_config_path()
    if not cfg_path.exists():
        return None
    with cfg_path.open("rb") as f:
        data = tomllib.load(f)
    cfg = DevlogConfig(
        sources=list(data.get("sources") or DEFAULT_SOURCES),
        claude_root=str(data.get("claude_root", "~/.claude")),
        codex_root=str(data.get("codex_root", "~/.codex")),
        cursor_root=str(data.get("cursor_root", "~/.cursor")),
        repo_path=str(data.get("repo_path", default_repo_path())).replace("\\", "/"),
        publish_mode=str(data.get("publish_mode", "auto")),
        schedule_time=str(data.get("schedule_time", "06:30")),
        remote=str(data.get("remote", "origin")),
        branch=str(data.get("branch", "main")),
    )
    cfg.validate()
    return cfg


def save_config(cfg: DevlogConfig, path: Path | None = None) -> Path:
    cfg.validate()
    cfg_path = path or default_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    sources = ", ".join(f'"{s}"' for s in cfg.sources)
    body = (
        f"sources = [{sources}]\n"
        f'claude_root = "{cfg.claude_root}"\n'
        f'codex_root = "{cfg.codex_root}"\n'
        f'cursor_root = "{cfg.cursor_root}"\n'
        f'repo_path = "{cfg.repo_path}"\n'
        f'publish_mode = "{cfg.publish_mode}"\n'
        f'schedule_time = "{cfg.schedule_time}"\n'
        f'remote = "{cfg.remote}"\n'
        f'branch = "{cfg.branch}"\n'
    )
    cfg_path.write_text(body, encoding="utf-8")
    return cfg_path


def config_to_dict(cfg: DevlogConfig) -> dict:
    return asdict(cfg)
