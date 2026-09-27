"""Static site builder: posts/*.md → docs/log/*.html feed."""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from importlib import resources
from pathlib import Path

from devlog.gitutil import GitRunner, default_git
from devlog.hidden import load_hidden_dates
from devlog.status import load_status

_TEMPLATES = resources.files("devlog") / "templates"
_PLACEHOLDER_RE = re.compile(r"@@([A-Z_]+)@@")


def _template(name: str) -> str:
    """HTML/CSS/JS kept as real files (devlog/templates/) instead of f-strings."""
    # Normalize: a Windows checkout with core.autocrlf would otherwise leak \r.
    return (_TEMPLATES / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def _fill(template: str, **values: str) -> str:
    """Substitute @@NAME@@ markers in one pass, so inserted text is never re-scanned."""
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")

DELETE_WORKFLOW_FILE = "delete-post.yml"
_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$")

SHARED_CSS = _template("shared.css")

ADMIN_CSS = _template("admin.css")

FONT_LINKS = (
    '  <link rel="preconnect" href="https://fonts.googleapis.com" />\n'
    '  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />\n'
    '  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,'
    "500;9..144,700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;"
    '500&display=swap" rel="stylesheet" />\n'
)

THEME_BOOT = """  <script>
    (function () {
      try {
        var t = localStorage.getItem("devlog-theme");
        document.documentElement.setAttribute("data-theme", t === "dark" ? "dark" : "light");
      } catch (e) {
        document.documentElement.setAttribute("data-theme", "light");
      }
    })();
  </script>
"""

THEME_TOGGLE = (
    '  <button type="button" class="theme-toggle" id="theme-toggle" '
    'aria-label="Switch to dark theme">\n'
    '    <span class="icon icon-sun" aria-hidden="true">☀</span>\n'
    '    <span class="icon icon-moon" aria-hidden="true">☾</span>\n'
    '    <span class="label">Dark</span>\n'
    "  </button>\n"
)


def list_posts(posts_dir: Path) -> list[tuple[date, Path, str]]:
    """Return (date, path, body) newest-first."""
    if not posts_dir.exists():
        return []
    items: list[tuple[date, Path, str]] = []
    for path in posts_dir.glob("*.md"):
        m = _DATE_RE.match(path.name)
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        body = path.read_text(encoding="utf-8")
        items.append((d, path, body))
    items.sort(key=lambda t: t[0], reverse=True)
    return items


def detect_github_repo(repo_path: Path, git_run: GitRunner = default_git) -> str | None:
    """Best-effort 'owner/repo' derived from the origin remote, for the admin delete panel.

    Returns None (never raises) whenever detection isn't possible -- no .git
    directory, no origin remote, or a remote that isn't a github.com URL --
    so the feed just renders without the admin panel in those cases.
    """
    repo_path = Path(repo_path)
    if not (repo_path / ".git").exists():
        return None
    result = git_run(["git", "remote", "get-url", "origin"], repo_path)
    if result.returncode != 0:
        return None
    match = _GITHUB_REMOTE_RE.search(result.stdout.strip())
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"


def _post_plain(body: str) -> str:
    """Strip a leading # title line if present; return remaining markdown-ish text."""
    lines = body.strip().splitlines()
    if lines and lines[0].startswith("#"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _excerpt(body: str, limit: int = 160) -> str:
    plain = _post_plain(body).replace("\n", " ").strip()
    if len(plain) <= limit:
        return plain
    return plain[: limit - 1].rstrip() + "…"


def _md_to_paragraphs(body: str) -> str:
    plain = _post_plain(body)
    if not plain:
        return "<p></p>"
    parts = [p.strip() for p in re.split(r"\n\s*\n", plain) if p.strip()]
    if not parts:
        parts = [plain]
    return "\n".join(f"<p>{html.escape(p)}</p>" for p in parts)


def _page(title: str, body_html: str) -> str:
    return _fill(
        _template("page.html"),
        TITLE=html.escape(title),
        THEME_BOOT=THEME_BOOT,
        FONT_LINKS=FONT_LINKS,
        SHARED_CSS=SHARED_CSS,
        THEME_TOGGLE=THEME_TOGGLE,
        BODY=body_html,
    )


def build_day_html(day: date, body: str) -> str:
    title = day.isoformat()
    inner = f"""<h1>{html.escape(title)}</h1>
<p class="meta">Daily build log</p>
<div class="post-body">
{_md_to_paragraphs(body)}
</div>
"""
    return _page(title, inner)


def _js_string(value: str) -> str:
    return json.dumps(value).replace("</", "<\\/")


def _admin_panel_html(
    github_repo: str,
    branch: str,
    hidden_dates: list[str] | None = None,
) -> str:
    hidden_dates = hidden_dates or []
    if hidden_dates:
        hidden_items = "\n".join(
            "<li>"
            f'<span>{html.escape(day)}</span>'
            f'<button type="button" class="unhide-btn" data-date="{html.escape(day)}">'
            "Unhide</button>"
            "</li>"
            for day in hidden_dates
        )
        hidden_block = (
            '<p class="status">Hidden from the public feed '
            "(markdown kept in <code>posts/</code>):</p>\n"
            f'<ul class="hidden-list" id="devlog-hidden-list">\n{hidden_items}\n</ul>'
        )
    else:
        hidden_block = (
            '<p class="status" id="devlog-hidden-list">No soft-hidden posts.</p>'
        )
    return _fill(
        _template("admin_panel.html"),
        REPO_HTML=html.escape(github_repo),
        HIDDEN_BLOCK=hidden_block,
        REPO_JS=_js_string(github_repo),
        WORKFLOW_JS=_js_string(DELETE_WORKFLOW_FILE),
        BRANCH_JS=_js_string(branch),
    )


def _friendly_timestamp(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso


def _format_status_line(status: dict) -> str:
    parts: list[str] = []
    pub_date = status.get("last_published_date")
    pub_at = status.get("last_published_at")
    if pub_date and pub_at:
        parts.append(f"Last published: {pub_date} ({_friendly_timestamp(pub_at)})")
    del_date = status.get("last_deleted_date")
    del_at = status.get("last_deleted_at")
    if del_date and del_at:
        parts.append(f"Last deleted: {del_date} ({_friendly_timestamp(del_at)})")
    hid_date = status.get("last_hidden_date")
    hid_at = status.get("last_hidden_at")
    if hid_date and hid_at:
        parts.append(f"Last hidden: {hid_date} ({_friendly_timestamp(hid_at)})")
    return " · ".join(parts)


def build_feed_html(
    posts: list[tuple[date, Path, str]],
    github_repo: str | None = None,
    branch: str = "main",
    status: dict | None = None,
    hidden_dates: list[str] | None = None,
) -> str:
    hidden_dates = hidden_dates or []
    if not posts:
        items = '<li><p class="excerpt">No posts yet.</p></li>'
    else:
        chunks: list[str] = []
        for day, _path, body in posts:
            iso = day.isoformat()
            href = f"{iso}.html"
            manage_btns = ""
            if github_repo:
                manage_btns = (
                    f'<button type="button" class="hide-btn" data-date="{iso}">Hide</button>'
                    f'<button type="button" class="delete-btn" data-date="{iso}">Delete</button>'
                )
            chunks.append(
                "<li>"
                f'<a href="{href}">{html.escape(iso)}</a>'
                f"{manage_btns}"
                f'<p class="excerpt">{html.escape(_excerpt(body))}</p>'
                "</li>"
            )
        items = "\n".join(chunks)
    admin_html = (
        _admin_panel_html(github_repo, branch, hidden_dates=hidden_dates)
        if github_repo
        else ""
    )
    admin_css = f"<style>{ADMIN_CSS}</style>" if github_repo else ""
    status_line = _format_status_line(status) if status else ""
    status_html = (
        f'<p class="meta status-line">{html.escape(status_line)}</p>' if status_line else ""
    )
    inner = f"""<h1>Log</h1>
<p class="meta">Reverse-chronological daily build logs</p>
{status_html}
<ul class="feed">
{items}
</ul>
{admin_html}
{admin_css}
"""
    return _page("Log", inner)


def write_post_markdown(posts_dir: Path, day: date, post_body: str, *, force: bool = False) -> Path:
    posts_dir.mkdir(parents=True, exist_ok=True)
    path = posts_dir / f"{day.isoformat()}.md"
    if path.exists() and not force:
        raise FileExistsError(str(path))
    path.write_text(f"# {day.isoformat()}\n\n{post_body.strip()}\n", encoding="utf-8")
    return path


def rebuild_site(
    repo_path: Path, git_run: GitRunner = default_git, branch: str = "main"
) -> list[Path]:
    """Rebuild docs/log from posts/*.md.

    Returns every path whose resulting git state should be staged, including
    stale HTML paths that were removed.
    """
    repo_path = Path(repo_path)
    posts_dir = repo_path / "posts"
    log_dir = repo_path / "docs" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    nojekyll = repo_path / "docs" / ".nojekyll"
    if not nojekyll.exists():
        nojekyll.write_text("", encoding="utf-8")
        written.append(nojekyll)

    posts = list_posts(posts_dir)
    hidden = load_hidden_dates(repo_path)
    hidden_sorted = sorted(hidden, reverse=True)
    visible = [(d, p, b) for d, p, b in posts if d.isoformat() not in hidden]
    github_repo = detect_github_repo(repo_path, git_run)
    status = load_status(repo_path)
    feed_path = log_dir / "index.html"
    feed_path.write_text(
        build_feed_html(
            visible,
            github_repo=github_repo,
            branch=branch,
            status=status,
            hidden_dates=hidden_sorted,
        ),
        encoding="utf-8",
    )
    written.append(feed_path)

    existing = {p.name for p in log_dir.glob("????-??-??.html")}
    keep: set[str] = set()
    for day, _path, body in visible:
        name = f"{day.isoformat()}.html"
        keep.add(name)
        day_path = log_dir / name
        day_path.write_text(build_day_html(day, body), encoding="utf-8")
        written.append(day_path)

    for stale in existing - keep:
        stale_path = log_dir / stale
        stale_path.unlink(missing_ok=True)
        written.append(stale_path)

    landing = repo_path / "docs" / "index.html"
    if _ensure_landing_nav(landing):
        written.append(landing)
    return written


def _ensure_landing_nav(index_path: Path) -> bool:
    """Insert a Log link into the landing CTA area if missing."""
    if not index_path.exists():
        return False
    text = index_path.read_text(encoding="utf-8")
    if 'href="log/index.html"' in text or "href='log/index.html'" in text:
        return False
    needle = 'href="https://github.com/musicofthings/devlog">Open on GitHub →</a>'
    if needle not in text:
        return False
    replacement = (
        needle
        + '\n        <a class="cta ghost" href="log/index.html">Read the log →</a>'
    )
    index_path.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
    return True
