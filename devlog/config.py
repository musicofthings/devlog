"""Load and save ~/.config/devlog/config.toml."""

from __future__ import annotations

import os
import re
import tomllib
import typing
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

PUBLISH_MODES = ("auto", "pr", "manual", "review")
OBSIDIAN_ON_DELETE = ("preserve", "remove")
DEFAULT_SOURCES = [
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
DEFAULT_OBSIDIAN_FOLDER = "DevLog"
DEFAULT_OBSIDIAN_DAILY_FOLDER = "Daily"
DEFAULT_OBSIDIAN_ON_DELETE = "preserve"
# "manual" by default: posts can contain raw user prompts, so a human should
# review before anything is pushed to a public site.
DEFAULT_PUBLISH_MODE = "manual"
DEFAULT_MODEL = "claude-sonnet-5"
PUBLIC_DETAIL_LEVELS = ("summary", "projects", "verbatim")
DEFAULT_PUBLIC_DETAIL = "projects"
RELATED_BACKENDS = ("tfidf", "ollama")
POST_WRITERS = ("auto", "template", "ollama")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _norm_path(value: str) -> str:
    """Normalize to forward slashes so paths are TOML-safe and portable.

    Backslashes inside double-quoted TOML strings are escape sequences
    (C:\\Users -> invalid \\U escape), which would corrupt the config file.
    """
    return value.replace("\\", "/")


def default_opencode_root() -> str:
    """Data dir that contains opencode.db when present; else the usual OS default."""
    candidates: list[Path] = []
    localapp = os.environ.get("LOCALAPPDATA")
    appdata = os.environ.get("APPDATA")
    if localapp:
        candidates.append(Path(localapp) / "opencode")
    if appdata:
        candidates.append(Path(appdata) / "opencode")
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        candidates.append(Path(xdg) / "opencode")
    candidates.append(Path.home() / ".local" / "share" / "opencode")
    for cand in candidates:
        if (cand / "opencode.db").exists():
            return _norm_path(str(cand))
    if os.name == "nt" and localapp:
        return _norm_path(str(Path(localapp) / "opencode"))
    return "~/.local/share/opencode"


def default_warp_root() -> str:
    localapp = os.environ.get("LOCALAPPDATA")
    if localapp:
        return _norm_path(str(Path(localapp) / "warp" / "Warp"))
    return _norm_path(str(Path.home() / ".local" / "share" / "warp" / "Warp"))


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
    grok_root: str = "~/.grok"
    copilot_root: str = "~/.copilot"
    opencode_root: str = field(default_factory=default_opencode_root)
    warp_root: str = field(default_factory=default_warp_root)
    vitreous_root: str = "~/.vitreous"
    antigravity_root: str = "~/.gemini"
    repo_path: str = field(default_factory=lambda: str(default_repo_path()).replace("\\", "/"))
    publish_mode: str = DEFAULT_PUBLISH_MODE
    schedule_time: str = "06:30"
    remote: str = "origin"
    branch: str = "main"
    model: str = DEFAULT_MODEL
    allow_external_api: bool = False
    obsidian_vault: str = ""
    obsidian_folder: str = DEFAULT_OBSIDIAN_FOLDER
    obsidian_daily_folder: str = DEFAULT_OBSIDIAN_DAILY_FOLDER
    obsidian_on_delete: str = DEFAULT_OBSIDIAN_ON_DELETE
    # Folder name, repo name, or full path -> canonical project name (vault).
    project_aliases: dict[str, str] = field(default_factory=dict)
    # Extra vault topics: display name -> match terms (added to the built-in catalog).
    topics: dict[str, list[str]] = field(default_factory=dict)
    # How much of your prompts reaches the public post: "summary" (minutes and
    # project count), "projects" (names + work types), or "verbatim" (quotes the
    # first prompt per project). The private vault always keeps full detail.
    public_detail: str = DEFAULT_PUBLIC_DETAIL
    # Extra regexes redacted everywhere (MRNs, sample IDs, client names...).
    redact_patterns: list[str] = field(default_factory=list)
    # Built-in identifier patterns: mrn, dob, ssn, phone, email, or "clinical" (all).
    redact_presets: list[str] = field(default_factory=list)
    # Folders outside your projects where you launch Nextflow/Snakemake (vault only).
    pipeline_dirs: list[str] = field(default_factory=list)
    # Better BibTeX JSON-RPC endpoint for citekey lookups; "" turns lookups off.
    zotero_url: str = "http://localhost:23119/better-bibtex/json-rpc"
    # Let `devlog mcp` agents tick threads and append notes/decisions (vault only).
    mcp_write: bool = False
    # False: days without activity are mirrored to the vault but not published.
    publish_empty_days: bool = False
    # USD per million tokens [input, output, cache_read]; adds to/overrides the
    # built-in Claude table (see devlog/pricing.py). Vault-only cost estimates.
    model_prices: dict[str, list[float]] = field(default_factory=dict)
    # Optional local models (Ollama on this machine; see devlog/local_llm.py).
    related_backend: str = "tfidf"  # tfidf | ollama
    # Who writes the public post: auto (Claude API if allowed, else template),
    # template, or ollama (local model, same privacy-reduced digest).
    post_writer: str = "auto"
    period_retros: bool = False
    ollama_url: str = "http://localhost:11434"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_model: str = "llama3.2"

    def __post_init__(self) -> None:
        self.claude_root = _norm_path(self.claude_root)
        self.codex_root = _norm_path(self.codex_root)
        self.cursor_root = _norm_path(self.cursor_root)
        self.grok_root = _norm_path(self.grok_root)
        self.copilot_root = _norm_path(self.copilot_root)
        self.opencode_root = _norm_path(self.opencode_root)
        self.warp_root = _norm_path(self.warp_root)
        self.vitreous_root = _norm_path(self.vitreous_root)
        self.antigravity_root = _norm_path(self.antigravity_root)
        self.repo_path = _norm_path(self.repo_path)
        self.obsidian_vault = _norm_path(self.obsidian_vault)
        self.obsidian_folder = _norm_path(self.obsidian_folder)
        self.obsidian_daily_folder = _norm_path(self.obsidian_daily_folder)
        if isinstance(self.pipeline_dirs, list):
            self.pipeline_dirs = [_norm_path(d) if isinstance(d, str) else d
                                  for d in self.pipeline_dirs]

    def root_for(self, source: str) -> Path:
        """Data root for a source. Unknown names are a bug, not a fallback."""
        mapping = {
            "claude_code": self.claude_root,
            "codex": self.codex_root,
            "cursor": self.cursor_root,
            "grok": self.grok_root,
            "copilot": self.copilot_root,
            "opencode": self.opencode_root,
            "warp": self.warp_root,
            "vitreous": self.vitreous_root,
            "antigravity": self.antigravity_root,
        }
        if source not in mapping:
            raise KeyError(f"No data root configured for source {source!r}")
        return Path(mapping[source]).expanduser()

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
        if not isinstance(self.project_aliases, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v.strip()
            for k, v in self.project_aliases.items()
        ):
            raise ValueError("project_aliases must be a table of \"name\" = \"canonical name\"")
        if not isinstance(self.topics, dict) or not all(
            isinstance(k, str) and k.strip() and isinstance(v, list)
            and all(isinstance(t, str) and t.strip() for t in v)
            for k, v in self.topics.items()
        ):
            raise ValueError('topics must be a table of "Name" = ["term", ...]')
        if self.public_detail not in PUBLIC_DETAIL_LEVELS:
            raise ValueError(
                f"public_detail must be one of {', '.join(PUBLIC_DETAIL_LEVELS)}; "
                f"got {self.public_detail!r}"
            )
        if not isinstance(self.publish_empty_days, bool):
            raise ValueError("publish_empty_days must be true or false")
        if not isinstance(self.redact_patterns, list):
            raise ValueError("redact_patterns must be a list of regular expressions")
        for pattern in self.redact_patterns:
            try:
                compiled = re.compile(pattern)
            except (re.error, TypeError) as exc:
                raise ValueError(f"redact_patterns: invalid regex {pattern!r}: {exc}") from exc
            # "", "x*", "a?" match the empty string, so they would redact
            # between every character instead of matching an identifier.
            if compiled.fullmatch(""):
                raise ValueError(
                    f"redact_patterns: {pattern!r} matches the empty string; "
                    "it must require at least one character"
                )
        from devlog.privacy import REDACT_PRESETS

        if not isinstance(self.redact_presets, list) or not all(
            p in REDACT_PRESETS for p in self.redact_presets
        ):
            raise ValueError(
                f"redact_presets must be a list drawn from {', '.join(REDACT_PRESETS)}; "
                f"got {self.redact_presets!r}"
            )
        if not isinstance(self.pipeline_dirs, list) or not all(
            isinstance(d, str) and d.strip() for d in self.pipeline_dirs
        ):
            raise ValueError("pipeline_dirs must be a list of folder paths")
        if self.related_backend not in RELATED_BACKENDS:
            raise ValueError(
                f"related_backend must be one of {', '.join(RELATED_BACKENDS)}; "
                f"got {self.related_backend!r}"
            )
        if self.post_writer not in POST_WRITERS:
            raise ValueError(
                f"post_writer must be one of {', '.join(POST_WRITERS)}; got {self.post_writer!r}"
            )
        if not isinstance(self.mcp_write, bool):
            raise ValueError("mcp_write must be true or false")
        if not isinstance(self.period_retros, bool):
            raise ValueError("period_retros must be true or false")
        if not isinstance(self.model_prices, dict) or not all(
            isinstance(v, list) and len(v) == 3
            and all(isinstance(x, (int, float)) and x >= 0 for x in v)
            for v in self.model_prices.values()
        ):
            raise ValueError(
                'model_prices must be a table of "model" = [input, output, cache_read] '
                "(USD per million tokens)"
            )
        if self.obsidian_on_delete not in OBSIDIAN_ON_DELETE:
            raise ValueError(
                f"obsidian_on_delete must be one of {', '.join(OBSIDIAN_ON_DELETE)}; "
                f"got {self.obsidian_on_delete!r}"
            )


def _field_types() -> dict[str, object]:
    return typing.get_type_hints(DevlogConfig)


def load_config(path: Path | None = None) -> DevlogConfig | None:
    """Read config.toml. Every DevlogConfig field is optional in the file."""
    cfg_path = path or default_config_path()
    if not cfg_path.exists():
        return None
    with cfg_path.open("rb") as fh:
        data = tomllib.load(fh)
    types = _field_types()
    kwargs: dict[str, typing.Any] = {}
    for f in fields(DevlogConfig):
        if f.name not in data:
            continue
        value = data[f.name]
        if types[f.name] is str:
            value = str(value)
        kwargs[f.name] = value
    if not kwargs.get("sources"):
        kwargs["sources"] = list(DEFAULT_SOURCES)
    cfg = DevlogConfig(**kwargs)
    cfg.validate()
    return cfg


def _toml_str(value: str) -> str:
    """Render a TOML basic string, escaping backslashes and double quotes."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return _toml_str(str(value))


def save_config(cfg: DevlogConfig, path: Path | None = None) -> Path:
    cfg.validate()
    cfg_path = path or default_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    scalars: list[str] = []
    tables: list[str] = []
    for f in fields(cfg):
        value = getattr(cfg, f.name)
        if isinstance(value, dict):
            # A TOML table must come after every top-level key.
            tables.append(f"\n[{f.name}]\n" + "".join(
                f"{_toml_str(k)} = {_toml_value(v)}\n" for k, v in sorted(value.items())
            ))
        else:
            scalars.append(f"{f.name} = {_toml_value(value)}\n")
    cfg_path.write_text("".join(scalars) + "".join(tables), encoding="utf-8")
    return cfg_path


def config_to_dict(cfg: DevlogConfig) -> dict:
    return asdict(cfg)
