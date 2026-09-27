"""`devlog audit`: find (and fix) privacy leaks in posts that are already public.

The noise filter and redaction only apply to posts generated after they
shipped. This scans every post in `posts/` — hidden ones too, since their
markdown is still in the public repository — for:

- harness text (`<mcp_meta_tools>`, `# AGENTS.md instructions`, skill preambles)
- credentials (API keys, tokens)
- identifiers matching `redact_patterns` / `redact_presets` (MRNs, DOBs, …)
- absolute paths that include a user name (`C:\\Users\\name`, `/home/name`)

and, as notes that don't fail the audit, local folder layouts (`~\\OneDrive\\…`),
your login name used as a word (a session started in your home folder used to
be named after it), and continuation nudges quoted as the day's task
("Continue from where you left off").

`--fix` rewrites the affected posts in place: harness and low-signal task
clauses are dropped from the template's "I worked on …" sentence (the
project still appears in "across …"), other sentences with harness text are
dropped, and everything else is redacted. The site is rebuilt and the change
committed and pushed like `devlog hide`, unless `--no-commit`. The rewrite
can't recover what the harness text displaced; for a full regeneration from
transcripts use `devlog publish --date … --force`.

Git history still holds the old text. Removing it needs a history rewrite
(e.g. `git filter-repo`) and a force-push, which this command never does.
"""

from __future__ import annotations

import argparse
import getpass
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from devlog.config import DevlogConfig, default_config_path, load_config
from devlog.gitutil import (
    GitPublishError,
    GitRunner,
    commit_and_push,
    default_git,
    git_paths,
    undo_local_commit,
)
from devlog.knowledge import _TEMPLATE_RE
from devlog.noise import find_injected, is_injected_prompt, is_low_signal_prompt
from devlog.privacy import (
    configure_redaction,
    identifier_matches,
    redact_sensitive_text,
    secret_matches,
)
from devlog.projects import HOME_PROJECT
from devlog.site import rebuild_site

LEAKS = ("harness", "secret", "identifier", "user-path")
NOTES = ("local-path", "user-name", "low-signal")
_POST_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
_USER_PATH_RE = re.compile(
    r"(?i)(?:\b[A-Z]:[\\/]+Users[\\/]+|(?<![\w~])/(?:Users|home)/)(?P<user>[^\\/\s`'\"]+)"
)
_LOCAL_PATH_RE = re.compile(r"~[\\/][^\s`'\"<>]*[\\/][^\s`'\"<>]+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Finding:
    kind: str
    text: str

    @property
    def is_leak(self) -> bool:
        return self.kind in LEAKS

    def shown(self) -> str:
        """Excerpt safe to print: identifiers and secrets are masked."""
        text = re.sub(r"\s+", " ", self.text).strip()
        if self.kind in {"secret", "identifier"}:
            return f"{text[:3]}… ({len(text)} chars)"
        return text if len(text) <= 60 else text[:59] + "…"


def _task_clauses(match: re.Match) -> list[tuple[str, str]]:
    """(project, task) pairs of the template's "I worked on" sentence."""
    names = [n.strip() for n in (match.group("projects") or "").split(",") if n.strip()]
    tasks = match.group("tasks") or ""
    if names:
        splitter = re.compile("; (?=(?:" + "|".join(re.escape(n) for n in names) + "): )")
        chunks = splitter.split(tasks)
    else:
        chunks = tasks.split("; ")
    out = []
    for chunk in chunks:
        name, sep, task = chunk.partition(": ")
        out.append((name, task) if sep else ("", chunk))
    return out


def _bad_task(task: str) -> str | None:
    if find_injected(task) or is_injected_prompt(task):
        return "harness"
    if is_low_signal_prompt(task):
        return "low-signal"
    return None


def _login_names() -> list[str]:
    names = {Path.home().name}
    try:
        names.add(getpass.getuser())
    except (OSError, KeyError, ImportError):
        pass
    # Generic account names would match ordinary words.
    return sorted(n for n in names if len(n) >= 3 and n.lower() not in {"root", "user", "admin"})


def audit_text(text: str, login_names: list[str] | None = None) -> list[Finding]:
    """Everything in a post body that shouldn't be public (or is noise)."""
    findings = [Finding("harness", m) for m in find_injected(text)]
    findings += [Finding("secret", m) for m in secret_matches(text)]
    findings += [Finding("identifier", m) for m in identifier_matches(text)]
    findings += [Finding("user-path", m.group(0)) for m in _USER_PATH_RE.finditer(text)]
    findings += [Finding("local-path", m.group(0)) for m in _LOCAL_PATH_RE.finditer(text)]
    for name in _login_names() if login_names is None else login_names:
        if _login_re(name).search(text):
            findings.append(Finding("user-name", name))
    match = _TEMPLATE_RE.search(" ".join(text.split()))
    if match is not None:
        for name, task in _task_clauses(match):
            if _bad_task(task) == "low-signal":
                findings.append(Finding("low-signal", f"{name}: {task}" if name else task))
    return list(dict.fromkeys(findings))


def _scrub_template(body: str) -> str | None:
    flat = " ".join(body.split())
    match = _TEMPLATE_RE.search(flat)
    if match is None or match.group("tasks") is None:
        return None
    kept = [f"{name}: {task}" if name else task
            for name, task in _task_clauses(match) if _bad_task(task) is None]
    sentence = f"I worked on {'; '.join(kept)}. " if kept else ""
    start, end = match.span("tasks")
    # The group excludes "I worked on " before it and ". " after it.
    head = flat[: start - len("I worked on ")]
    tail = flat[end:].removeprefix(".").lstrip()
    return (head + sentence + tail).strip()


def _drop_harness_sentences(body: str) -> str:
    lines = []
    for line in body.split("\n"):
        sentences = _SENTENCE_RE.split(line)
        kept = [s for s in sentences if not find_injected(s)]
        if kept != sentences and not "".join(kept).strip():
            continue
        lines.append(" ".join(kept) if kept != sentences else line)
    return "\n".join(lines)


def _login_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w/\\]){re.escape(name)}(?![\w/\\])", re.IGNORECASE)


def scrub_text(body: str, login_names: list[str] | None = None) -> str:
    """The post body with harness text, noise, and sensitive values removed.

    Your login name becomes "home", which is what sessions started in the
    home folder are called now.
    """
    scrubbed = _scrub_template(body) or body
    scrubbed = _drop_harness_sentences(scrubbed)
    scrubbed = redact_sensitive_text(scrubbed)
    scrubbed = _USER_PATH_RE.sub(lambda m: m.group(0).replace(m.group("user"), "[USER]"),
                                 scrubbed)
    for name in _login_names() if login_names is None else login_names:
        scrubbed = _login_re(name).sub(HOME_PROJECT, scrubbed)
    return scrubbed.strip() or "No coding activity logged today."


def _split_post(markdown: str) -> tuple[str, str]:
    text = markdown.replace("\r\n", "\n")
    head, sep, rest = text.partition("\n")
    if head.startswith("# ") and sep:
        return head, rest.strip()
    return "", text.strip()


def audit_posts(posts_dir: Path, only: date | None = None) -> list[tuple[date, Path, list]]:
    results = []
    for path in sorted(posts_dir.glob("*.md")):
        match = _POST_RE.match(path.name)
        if match is None:
            continue
        day = date.fromisoformat(match.group(1))
        if only is not None and day != only:
            continue
        _, body = _split_post(path.read_text(encoding="utf-8"))
        findings = audit_text(body)
        if findings:
            results.append((day, path, findings))
    return results


def fix_posts(results: list[tuple[date, Path, list]]) -> list[tuple[date, Path]]:
    """Rewrite each flagged post; returns the ones that changed."""
    changed = []
    for day, path, _findings in results:
        original = path.read_text(encoding="utf-8")
        heading, body = _split_post(original)
        fixed = f"{heading or f'# {day.isoformat()}'}\n\n{scrub_text(body)}\n"
        if fixed != original.replace("\r\n", "\n"):
            path.write_text(fixed, encoding="utf-8")
            changed.append((day, path))
    return changed


def _commit_fix(cfg: DevlogConfig, repo: Path, changed: list[tuple[date, Path]],
                originals: dict[Path, str], git_run: GitRunner) -> None:
    written = rebuild_site(repo, git_run=git_run, branch=cfg.branch)
    artifacts = [p for _, p in changed] + written
    days = ", ".join(d.isoformat() for d, _ in changed)
    try:
        commit_and_push(repo, f"audit: scrub {len(changed)} post(s) ({days})", artifacts,
                        remote=cfg.remote, branch=cfg.branch, git_run=git_run,
                        require_changes=True)
    except RuntimeError as exc:
        if isinstance(exc, GitPublishError) and exc.committed:
            note = undo_local_commit(repo, git_run, "audit") or "local audit commit was reset"
        else:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")
            try:
                others = [p for p in written if p not in originals]
                paths = git_paths(repo, others) if others else []
                if paths:
                    git_run(["git", "checkout", "HEAD", "--", *paths], repo)
            except RuntimeError:
                pass
            note = "posts restored in the working tree"
        raise RuntimeError(f"{exc} ({note})") from exc


def _mirror(cfg: DevlogConfig, repo: Path, changed: list[tuple[date, Path]]) -> str | None:
    from devlog.obsidian import backfill_posts

    for day, _ in changed:
        outcome = backfill_posts(cfg, repo / "posts", target=day)
        if outcome["status"] == "disabled":
            return None
        if outcome["status"] not in {"written", "dry_run"}:
            return f"vault not updated: {outcome.get('status')} {outcome.get('error', '')}"
    return f"vault: {len(changed)} day note(s) updated"


def cmd_audit(argv: list[str] | None = None, *, git_run: GitRunner = default_git) -> int:
    parser = argparse.ArgumentParser(
        prog="devlog audit",
        description="Scan published posts for harness text, secrets, identifiers, and paths",
    )
    parser.add_argument("--date", default=None, help="Audit one day (YYYY-MM-DD)")
    parser.add_argument("--fix", action="store_true",
                        help="Rewrite flagged posts, rebuild the site, commit and push")
    parser.add_argument("--no-commit", action="store_true",
                        help="With --fix: leave the rewritten files uncommitted for review")
    parser.add_argument("--repo", type=Path, default=None,
                        help="Site repository (default: repo_path from config)")
    parser.add_argument("--config", type=Path, default=None,
                        help="Config path (default: ~/.config/devlog/config.toml)")
    args = parser.parse_args(argv)

    cfg_path = args.config or default_config_path()
    try:
        cfg = load_config(cfg_path)
    except (OSError, ValueError) as exc:
        print(f"Could not load config at {cfg_path}: {exc}")
        return 2
    if cfg is None and args.repo is None:
        print(f"No config at {cfg_path}. Run: devlog init (or pass --repo)")
        return 2
    cfg = cfg or DevlogConfig(repo_path=str(args.repo))
    repo = (args.repo or Path(cfg.repo_path)).expanduser()
    posts_dir = repo / "posts"
    if not posts_dir.is_dir():
        print(f"No posts directory at {posts_dir}")
        return 2
    only = None
    if args.date:
        try:
            only = date.fromisoformat(args.date)
        except ValueError:
            print(f"Invalid --date {args.date!r}: expected YYYY-MM-DD")
            return 2

    configure_redaction(cfg.redact_patterns, cfg.redact_presets)
    results = audit_posts(posts_dir, only)
    total = len(list(posts_dir.glob("*.md"))) if only is None else 1
    for day, _path, findings in results:
        for f in findings:
            level = "LEAK" if f.is_leak else "note"
            print(f"{day}  {level:4}  {f.kind:10}  {f.shown()}")
    leaky = [r for r in results if any(f.is_leak for f in r[2])]
    print(f"\n{total} post(s) scanned: {len(leaky)} with leaks, "
          f"{len(results) - len(leaky)} with notes only.")
    if not args.fix:
        if results:
            print("Run `devlog audit --fix` to rewrite them "
                  "(or `devlog publish --date … --force` to regenerate from transcripts).")
        return 1 if leaky else 0

    originals = {p: p.read_text(encoding="utf-8") for _, p, _ in results}
    changed = fix_posts(results)
    if not changed:
        print("Nothing to rewrite.")
        return 1 if leaky else 0
    remaining = [r for r in audit_posts(posts_dir, only) if any(f.is_leak for f in r[2])]
    print(f"Rewrote {len(changed)} post(s): " + ", ".join(d.isoformat() for d, _ in changed))
    if args.no_commit:
        rebuild_site(repo, git_run=git_run, branch=cfg.branch)
        print("Site rebuilt; changes left uncommitted (review with `git diff`).")
    else:
        try:
            _commit_fix(cfg, repo, changed, {p: originals[p] for _, p in changed}, git_run)
        except RuntimeError as exc:
            print(f"Audit fix failed: {exc}")
            return 1
        print("Committed and pushed.")
    vault = _mirror(cfg, repo, changed)
    if vault:
        print(vault)
    for day, _path, findings in remaining:
        print(f"still flagged: {day} " + ", ".join(f.kind for f in findings if f.is_leak))
    print("Note: git history still has the old text; only a history rewrite removes it.")
    return 1 if remaining else 0
