"""Static site builder: posts/*.md → docs/log/*.html feed."""

from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")

SHARED_CSS = """
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }
body {
  font-family: "IBM Plex Sans", system-ui, sans-serif;
  color: var(--foam);
  background:
    radial-gradient(
      900px 480px at 85% -5%,
      color-mix(in srgb, var(--amber) 14%, transparent),
      transparent 55%
    ),
    linear-gradient(165deg, var(--bg-accent) 0%, var(--bg) 50%, var(--bg-accent) 100%);
  min-height: 100vh;
  transition: background 180ms ease, color 180ms ease;
}
a { color: var(--amber); text-decoration: none; }
a:hover { text-decoration: underline; }
.wrap {
  max-width: 42rem;
  margin: 0 auto;
  padding: clamp(1.5rem, 4vw, 3rem);
  padding-top: clamp(3.5rem, 6vw, 4.5rem);
}
.nav {
  display: flex;
  align-items: center;
  gap: 1.25rem;
  margin-bottom: 2rem;
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.85rem;
}
h1, h2 {
  font-family: "Fraunces", Georgia, serif;
  color: var(--ink);
  font-weight: 700;
  letter-spacing: -0.02em;
}
html[data-theme="dark"] h1,
html[data-theme="dark"] h2 { color: var(--paper); }
h1 { font-size: clamp(2rem, 5vw, 3rem); margin: 0 0 0.75rem; }
.meta { color: var(--mist); font-size: 0.95rem; margin-bottom: 1.75rem; }
.post-body {
  line-height: 1.65;
  font-size: 1.08rem;
  color: var(--foam);
}
.post-body p { margin: 0 0 1rem; }
.feed { list-style: none; padding: 0; margin: 0; }
.feed li {
  border-top: 1px solid var(--line);
  padding: 1.1rem 0;
}
.feed li:last-child { border-bottom: 1px solid var(--line); }
.feed a {
  font-family: "IBM Plex Mono", monospace;
  font-size: 1rem;
}
.feed .excerpt {
  margin: 0.45rem 0 0;
  color: var(--mist);
  line-height: 1.5;
}
"""

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
        d = date.fromisoformat(m.group(1))
        body = path.read_text(encoding="utf-8")
        items.append((d, path, body))
    items.sort(key=lambda t: t[0], reverse=True)
    return items


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
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)} · Daily Dev Log</title>
{THEME_BOOT}
  {FONT_LINKS}
  <link rel="stylesheet" href="../assets/theme.css" />
  <style>{SHARED_CSS}</style>
</head>
<body>
{THEME_TOGGLE}
  <div class="wrap">
    <nav class="nav">
      <a href="../index.html">Home</a>
      <a href="index.html">Log</a>
    </nav>
    {body_html}
  </div>
  <script src="../assets/theme.js"></script>
</body>
</html>
"""


def build_day_html(day: date, body: str) -> str:
    title = day.isoformat()
    inner = f"""<h1>{html.escape(title)}</h1>
<p class="meta">Daily build log</p>
<div class="post-body">
{_md_to_paragraphs(body)}
</div>
"""
    return _page(title, inner)


def build_feed_html(posts: list[tuple[date, Path, str]]) -> str:
    if not posts:
        items = '<li><p class="excerpt">No posts yet.</p></li>'
    else:
        chunks: list[str] = []
        for day, _path, body in posts:
            href = f"{day.isoformat()}.html"
            chunks.append(
                "<li>"
                f'<a href="{href}">{html.escape(day.isoformat())}</a>'
                f'<p class="excerpt">{html.escape(_excerpt(body))}</p>'
                "</li>"
            )
        items = "\n".join(chunks)
    inner = f"""<h1>Log</h1>
<p class="meta">Reverse-chronological daily build logs</p>
<ul class="feed">
{items}
</ul>
"""
    return _page("Log", inner)


def write_post_markdown(posts_dir: Path, day: date, post_body: str, *, force: bool = False) -> Path:
    posts_dir.mkdir(parents=True, exist_ok=True)
    path = posts_dir / f"{day.isoformat()}.md"
    if path.exists() and not force:
        raise FileExistsError(str(path))
    path.write_text(f"# {day.isoformat()}\n\n{post_body.strip()}\n", encoding="utf-8")
    return path


def rebuild_site(repo_path: Path) -> list[Path]:
    """Rebuild docs/log from posts/*.md. Returns written paths."""
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
    feed_path = log_dir / "index.html"
    feed_path.write_text(build_feed_html(posts), encoding="utf-8")
    written.append(feed_path)

    existing = {p.name for p in log_dir.glob("????-??-??.html")}
    keep: set[str] = set()
    for day, _path, body in posts:
        name = f"{day.isoformat()}.html"
        keep.add(name)
        day_path = log_dir / name
        day_path.write_text(build_day_html(day, body), encoding="utf-8")
        written.append(day_path)

    for stale in existing - keep:
        (log_dir / stale).unlink(missing_ok=True)

    _ensure_landing_nav(repo_path / "docs" / "index.html")
    return written


def _ensure_landing_nav(index_path: Path) -> None:
    """Insert a Log link into the landing CTA area if missing."""
    if not index_path.exists():
        return
    text = index_path.read_text(encoding="utf-8")
    if 'href="log/index.html"' in text or "href='log/index.html'" in text:
        return
    needle = 'href="https://github.com/musicofthings/devlog">Open on GitHub →</a>'
    if needle not in text:
        return
    replacement = (
        needle
        + '\n        <a class="cta ghost" href="log/index.html">Read the log →</a>'
    )
    index_path.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
