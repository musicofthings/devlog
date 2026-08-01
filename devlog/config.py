"""Load and save ~/.config/devlog/config.toml."""

from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

PUBLISH_MODES = ("auto", "pr", "manual")
DEFAULT_SOURCES = ["claude_code", "codex", "cursor"]
# "manual" by default: posts can contain raw user prompts, so a human should
# review before anything is pushed to a public site.
DEFAULT_PUBLISH_MODE = "manual"
DEFAULT_MODEL = "claude-sonnet-5"

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _norm_path(value: str) -> str:
    """Normalize to forward slashes so paths are TOML-safe and portable.

    Backslashes inside double-quoted TOML strings are escape sequences
    (C:\\Users -> invalid \\U escape), which would corrupt the config file.
    """
    return value.replace("\\", "/")


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
    publish_mode: str = DEFAULT_PUBLISH_MODE
    schedule_time: str = "06:30"
    remote: str = "origin"
    branch: str = "main"
    model: str = DEFAULT_MODEL
    allow_external_api: bool = False

    def __post_init__(self) -> None:
        self.claude_root = _norm_path(self.claude_root)
        self.codex_root = _norm_path(self.codex_root)
        self.cursor_root = _norm_path(self.cursor_root)
        self.repo_path = _norm_path(self.repo_path)

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
        if not _TIME_RE.match(self.schedule_time.strip()):
            raise ValueError(
                f"schedule_time must be HH:MM (24-hour); got {self.schedule_time!r}"
            )
        if not isinstance(self.allow_external_api, bool):
            raise ValueError("allow_external_api must be true or false")


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
        repo_path=str(data.get("repo_path", default_repo_path())),
        publish_mode=str(data.get("publish_mode", DEFAULT_PUBLISH_MODE)),
        schedule_time=str(data.get("schedule_time", "06:30")),
        remote=str(data.get("remote", "origin")),
        branch=str(data.get("branch", "main")),
        model=str(data.get("model", DEFAULT_MODEL)),
        allow_external_api=data.get("allow_external_api", False),
    )
    cfg.validate()
    return cfg


def _toml_str(value: str) -> str:
    """Render a TOML basic string, escaping backslashes and double quotes."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_config(cfg: DevlogConfig, path: Path | None = None) -> Path:
    cfg.validate()
    cfg_path = path or default_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    sources = ", ".join(_toml_str(s) for s in cfg.sources)
    body = (
        f"sources = [{sources}]\n"
        f"claude_root = {_toml_str(cfg.claude_root)}\n"
        f"codex_root = {_toml_str(cfg.codex_root)}\n"
        f"cursor_root = {_toml_str(cfg.cursor_root)}\n"
        f"repo_path = {_toml_str(cfg.repo_path)}\n"
        f"publish_mode = {_toml_str(cfg.publish_mode)}\n"
        f"schedule_time = {_toml_str(cfg.schedule_time)}\n"
        f"remote = {_toml_str(cfg.remote)}\n"
        f"branch = {_toml_str(cfg.branch)}\n"
        f"model = {_toml_str(cfg.model)}\n"
        f"allow_external_api = {'true' if cfg.allow_external_api else 'false'}\n"
    )
    cfg_path.write_text(body, encoding="utf-8")
    return cfg_path


def config_to_dict(cfg: DevlogConfig) -> dict:
    return asdict(cfg)
