"""`devlog doctor`: one command that says what's set up, what's missing, and how to fix it.

Checks, in order: config, log sources, the site repository (git, remote,
identity, last publish, audit), GitHub CLI, the Obsidian vault, optional
local services (Ollama, Zotero/Better BibTeX), the MCP extra, and the nightly
scheduled task. Problems exit 1; warnings are things that only limit a
feature. Output is plain ASCII so it survives a Windows scheduled-task log.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from devlog import __version__
from devlog.config import default_config_path, load_config

OK, WARN, FAIL = "ok", "warn", "FAIL"

Runner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]
Probe = Callable[[str], bool]


@dataclass
class Check:
    level: str
    area: str
    message: str
    hint: str = ""


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True, timeout=20)


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return 200 <= response.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500  # e.g. BBT answers GET with 4xx: it's there
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _ollama_models(url: str) -> list[str] | None:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)]


def _git(run: Runner, repo: Path, *args: str) -> str | None:
    try:
        out = run(["git", *args], repo)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def run_checks(
    cfg_path: Path,
    *,
    run: Runner = _run,
    which: Callable[[str], str | None] = shutil.which,
    http_ok: Probe = _http_ok,
    ollama_models: Callable[[str], list[str] | None] = _ollama_models,
    os_name: str = sys.platform,
    now: datetime | None = None,
) -> list[Check]:
    checks: list[Check] = []
    add = checks.append
    add(Check(OK, "devlog", f"{__version__} on Python {platform.python_version()}"))

    try:
        cfg = load_config(cfg_path)
    except (OSError, ValueError) as exc:
        add(Check(FAIL, "config", f"{cfg_path} is invalid: {exc}",
                  "fix the file or re-run devlog init"))
        return checks
    if cfg is None:
        add(Check(FAIL, "config", f"no config at {cfg_path}", "run: devlog init"))
        return checks
    add(Check(OK, "config", f"{cfg_path} (publish_mode={cfg.publish_mode}, "
                            f"public_detail={cfg.public_detail})"))
    if cfg.public_detail == "verbatim" and not (cfg.redact_patterns or cfg.redact_presets):
        add(Check(WARN, "privacy", "public_detail = verbatim with no redaction patterns",
                  'add redact_presets = ["clinical"] or your own redact_patterns'))

    # ---------------------------------------------------------------- sources
    found: list[str] = []
    missing: list[str] = []
    for source in cfg.sources:
        try:
            root = cfg.root_for(source)
        except KeyError:
            add(Check(FAIL, "sources", f"unknown source {source!r}", "remove it from sources"))
            continue
        (found if root.exists() else missing).append(source)
    if found:
        add(Check(OK, "sources", "logs found for " + ", ".join(found)))
    else:
        add(Check(FAIL, "sources", "no source has a data folder", "check the *_root settings"))
    if missing:
        add(Check(WARN, "sources", "no logs for " + ", ".join(missing),
                  "fine if you don't use them; otherwise set their *_root or drop them "
                  "from sources"))

    # ---------------------------------------------------------------- repo
    repo = Path(cfg.repo_path).expanduser()
    if not (repo / ".git").exists():
        add(Check(FAIL, "repo", f"{repo} is not a git checkout",
                  "set repo_path to your site clone"))
    else:
        remote = _git(run, repo, "remote", "get-url", cfg.remote)
        if remote:
            add(Check(OK, "repo", f"{repo} -> {cfg.remote} {remote} ({cfg.branch})"))
        else:
            add(Check(FAIL, "repo", f"no remote named {cfg.remote!r} in {repo}",
                      f"git remote add {cfg.remote} <url>"))
        if not _git(run, repo, "config", "user.email"):
            add(Check(WARN, "git", "git user.email is not set",
                      "commits can't be attributed to you in day notes"))
        from devlog.status import load_status

        last = load_status(repo).get("last_published_at")
        if last:
            try:
                age = (now or datetime.now(UTC)) - datetime.fromisoformat(last)
                level = WARN if age.days >= 3 and cfg.publish_mode in {"auto", "pr"} else OK
                add(Check(level, "publish", f"last publish {last[:16]} ({age.days} day(s) ago)",
                          "check the scheduled task and its log" if level == WARN else ""))
            except ValueError:
                pass
        else:
            add(Check(WARN, "publish", "nothing published yet", "run: devlog publish --dry-run"))
        if (repo / "posts").is_dir():
            from devlog.audit import audit_posts
            from devlog.privacy import configure_redaction

            configure_redaction(cfg.redact_patterns, cfg.redact_presets)
            leaks = [d for d, _, fs in audit_posts(repo / "posts") if any(f.is_leak for f in fs)]
            if leaks:
                add(Check(FAIL, "audit", f"{len(leaks)} published post(s) leak: "
                          + ", ".join(str(d) for d in leaks[:5]), "run: devlog audit"))
            else:
                add(Check(OK, "audit", "published posts are clean"))

    # ---------------------------------------------------------------- GitHub CLI
    if which("gh") is None:
        add(Check(WARN, "gh", "GitHub CLI not installed",
                  "needed for pull requests in day notes and open PRs on hubs"))
    else:
        try:
            status = run(["gh", "auth", "status"], Path.cwd())
            authed = status.returncode == 0
        except (OSError, subprocess.SubprocessError):
            authed = False
        add(Check(OK, "gh", "GitHub CLI signed in") if authed else
            Check(WARN, "gh", "GitHub CLI is not signed in", "run: gh auth login"))

    # ---------------------------------------------------------------- vault
    vault = Path(cfg.obsidian_vault).expanduser() if cfg.obsidian_vault.strip() else None
    if vault is None:
        add(Check(WARN, "vault", "obsidian_vault is not set", "run devlog init to add a vault"))
    elif not vault.is_dir():
        add(Check(FAIL, "vault", f"{vault} does not exist", "create it or fix obsidian_vault"))
    else:
        folder = (cfg.obsidian_folder or "DevLog").strip().strip("/\\")
        index = (vault / folder if folder else vault) / ".devlog" / "index.json"
        try:
            data = json.loads(index.read_text(encoding="utf-8"))
            days = data.get("days") or {}
            latest = max(days) if days else "none"
            add(Check(OK, "vault", f"{vault} ({len(days)} day(s), latest {latest}, "
                                   f"index v{data.get('version')})"))
        except (OSError, ValueError):
            add(Check(WARN, "vault", f"{vault} has no devlog index yet",
                      "run: devlog obsidian --backfill"))
        try:
            import mcp  # noqa: F401

            add(Check(OK, "mcp", "agent memory available: devlog mcp"
                                 + (" (write tools on)" if cfg.mcp_write else "")))
        except ImportError:
            add(Check(WARN, "mcp", "MCP SDK not installed (devlog mcp won't start)",
                      'pip install "daily-devlog[mcp]"'))

    # ---------------------------------------------------------------- local services
    wants_ollama = (cfg.post_writer == "ollama" or cfg.related_backend == "ollama"
                    or cfg.period_retros)
    if wants_ollama:
        models = ollama_models(cfg.ollama_url)
        if models is None:
            level = FAIL if cfg.post_writer == "ollama" else WARN
            add(Check(level, "ollama", f"no Ollama server at {cfg.ollama_url}",
                      "start Ollama (features fall back to the template / TF-IDF)"))
        else:
            wanted = {cfg.ollama_model} if cfg.post_writer == "ollama" or cfg.period_retros \
                else set()
            if cfg.related_backend == "ollama":
                wanted.add(cfg.ollama_embed_model)
            short = {m.split(":")[0] for m in models} | set(models)
            missing = sorted(m for m in wanted if m not in short)
            if missing:
                add(Check(WARN, "ollama", "models not pulled: " + ", ".join(missing),
                          "ollama pull " + " ".join(missing)))
            else:
                add(Check(OK, "ollama", f"{cfg.ollama_url} with {', '.join(sorted(wanted))}"))
    if cfg.zotero_url.strip() and vault is not None:
        if http_ok(cfg.zotero_url):
            add(Check(OK, "zotero", "Better BibTeX reachable; papers get [[@citekey]] links"))
        else:
            add(Check(WARN, "zotero", "Zotero/Better BibTeX not reachable",
                      'open Zotero to link citekeys, or set zotero_url = "" to stop trying'))

    # ---------------------------------------------------------------- scheduler
    if os_name == "win32":
        from devlog.scheduler import TASK_NAME

        try:
            query = run(["schtasks", "/Query", "/TN", TASK_NAME], Path.cwd())
            present = query.returncode == 0
        except (OSError, subprocess.SubprocessError):
            present = False
        add(Check(OK, "schedule", f"{TASK_NAME} is registered ({cfg.schedule_time})")
            if present else
            Check(FAIL if cfg.publish_mode in {"auto", "pr", "review"} else WARN, "schedule",
                  f"{TASK_NAME} is not registered", "run: devlog init (re-registers it)"))
    else:
        add(Check(OK, "schedule", "nightly task is Windows-only here; use cron/launchd "
                                  f"to run `devlog publish` at {cfg.schedule_time}"))
    return checks


def format_checks(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        lines.append(f"[{c.level:>4}] {c.area:<9} {c.message}")
        if c.hint and c.level != OK:
            lines.append(f"{'':17}-> {c.hint}")
    fails = sum(c.level == FAIL for c in checks)
    warns = sum(c.level == WARN for c in checks)
    lines.append("")
    lines.append(f"{fails} problem(s), {warns} warning(s)." if fails or warns
                 else "All good.")
    return "\n".join(lines)


def cmd_doctor(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devlog doctor",
                                     description="Check devlog's setup and say how to fix it")
    parser.add_argument("--config", type=Path, default=None,
                        help="Config path (default: ~/.config/devlog/config.toml)")
    args = parser.parse_args(argv)
    checks = run_checks(args.config or default_config_path())
    print(format_checks(checks))
    return 1 if any(c.level == FAIL for c in checks) else 0

