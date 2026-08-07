"""Static site builder: posts/*.md → docs/log/*.html feed."""

from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path

from devlog.gitutil import GitRunner, default_git

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")

DELETE_WORKFLOW_FILE = "delete-post.yml"
_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$")

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

ADMIN_CSS = """
.admin {
  margin-top: 2.5rem;
  padding-top: 1.5rem;
  border-top: 1px dashed var(--line);
}
.admin summary {
  cursor: pointer;
  font-family: "IBM Plex Mono", monospace;
  font-size: 0.85rem;
  color: var(--mist);
}
.admin .row {
  display: flex;
  gap: 0.5rem;
  margin: 0.75rem 0;
  flex-wrap: wrap;
}
.admin input[type="password"] {
  flex: 1;
  min-width: 12rem;
  font-family: "IBM Plex Mono", monospace;
  padding: 0.4rem 0.5rem;
}
.admin button {
  font-family: "IBM Plex Mono", monospace;
  cursor: pointer;
}
.admin .status {
  font-size: 0.8rem;
  color: var(--mist);
  margin-top: 0.5rem;
}
.feed .delete-btn {
  margin-left: 0.75rem;
  font-size: 0.78rem;
  font-family: "IBM Plex Mono", monospace;
  cursor: pointer;
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


def _js_string(value: str) -> str:
    return json.dumps(value).replace("</", "<\\/")


def _admin_panel_html(github_repo: str, branch: str) -> str:
    return f"""
<details class="admin">
  <summary>Admin: manage posts</summary>
  <p class="status">
    Paste a GitHub fine-grained personal access token scoped to
    <code>{html.escape(github_repo)}</code> with <strong>Actions: read and write</strong>
    permission only (not Contents). It is saved in this browser's local storage and
    never sent anywhere except api.github.com.
  </p>
  <div class="row">
    <input type="password" id="devlog-token-input" placeholder="github_pat_..."
           autocomplete="off" />
    <button type="button" id="devlog-token-save">Save token</button>
    <button type="button" id="devlog-token-clear">Clear token</button>
  </div>
  <p class="status" id="devlog-admin-status"></p>
</details>
<script>
(function () {{
  var REPO = {_js_string(github_repo)};
  var WORKFLOW = {_js_string(DELETE_WORKFLOW_FILE)};
  var BRANCH = {_js_string(branch)};
  var STORAGE_KEY = "devlog-admin-token";
  var statusEl = document.getElementById("devlog-admin-status");

  function setStatus(msg) {{
    if (statusEl) statusEl.textContent = msg;
  }}
  function getToken() {{
    try {{ return localStorage.getItem(STORAGE_KEY) || ""; }}
    catch (e) {{ return ""; }}
  }}

  var saveBtn = document.getElementById("devlog-token-save");
  var clearBtn = document.getElementById("devlog-token-clear");
  var input = document.getElementById("devlog-token-input");

  if (saveBtn) {{
    saveBtn.addEventListener("click", function () {{
      try {{
        localStorage.setItem(STORAGE_KEY, input.value.trim());
        setStatus("Token saved.");
      }} catch (e) {{
        setStatus("Could not save token: " + e);
      }}
    }});
  }}
  if (clearBtn) {{
    clearBtn.addEventListener("click", function () {{
      try {{
        localStorage.removeItem(STORAGE_KEY);
        input.value = "";
        setStatus("Token cleared.");
      }} catch (e) {{
        setStatus("Could not clear token: " + e);
      }}
    }});
  }}

  document.querySelectorAll(".delete-btn").forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      var day = btn.getAttribute("data-date");
      if (!confirm("Delete the " + day + " post? This pushes a real commit removing it.")) {{
        return;
      }}
      var token = getToken();
      if (!token) {{
        setStatus("Save a token first.");
        return;
      }}
      setStatus("Requesting delete of " + day + "...");
      fetch(
        "https://api.github.com/repos/" + REPO + "/actions/workflows/" + WORKFLOW + "/dispatches",
        {{
          method: "POST",
          headers: {{
            "Authorization": "token " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json"
          }},
          body: JSON.stringify({{ ref: BRANCH, inputs: {{ date: day }} }})
        }}
      ).then(function (resp) {{
        if (resp.status === 204) {{
          setStatus("Delete requested for " + day + " -- refresh in about 30 seconds.");
        }} else {{
          resp.text().then(function (text) {{
            setStatus("Delete request failed (" + resp.status + "): " + text);
          }});
        }}
      }}).catch(function (err) {{
        setStatus("Delete request failed: " + err);
      }});
    }});
  }});
}})();
</script>
"""


def build_feed_html(
    posts: list[tuple[date, Path, str]],
    github_repo: str | None = None,
    branch: str = "main",
) -> str:
    if not posts:
        items = '<li><p class="excerpt">No posts yet.</p></li>'
    else:
        chunks: list[str] = []
        for day, _path, body in posts:
            iso = day.isoformat()
            href = f"{iso}.html"
            delete_btn = (
                f'<button type="button" class="delete-btn" data-date="{iso}">Delete</button>'
                if github_repo
                else ""
            )
            chunks.append(
                "<li>"
                f'<a href="{href}">{html.escape(iso)}</a>'
                f"{delete_btn}"
                f'<p class="excerpt">{html.escape(_excerpt(body))}</p>'
                "</li>"
            )
        items = "\n".join(chunks)
    admin_html = _admin_panel_html(github_repo, branch) if github_repo else ""
    admin_css = f"<style>{ADMIN_CSS}</style>" if github_repo else ""
    inner = f"""<h1>Log</h1>
<p class="meta">Reverse-chronological daily build logs</p>
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
    github_repo = detect_github_repo(repo_path, git_run)
    feed_path = log_dir / "index.html"
    feed_path.write_text(
        build_feed_html(posts, github_repo=github_repo, branch=branch), encoding="utf-8"
    )
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
