"""Phase 2: project identity, commits, tokens, open threads, rollups, Bases."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from devlog.config import DevlogConfig, load_config, save_config
from devlog.knowledge import build_day_meta, parse_post_meta
from devlog.models import SessionDigest
from devlog.obsidian import try_mirror_post
from devlog.projects import ProjectResolver, find_git_root, read_remote_url, repo_web_url
from devlog.sources.claude_code import parse_session_file
from devlog.threads import extract_threads
from devlog.vault_graph import load_done_threads, thread_key

T0 = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)


COMMIT_DAY = date(2026, 8, 13)
# Noon local time on COMMIT_DAY, so --since/--until day bounds can't straddle it.
_NOON = datetime(2026, 8, 13, 12, 0).astimezone().isoformat()


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": _NOON, "GIT_COMMITTER_DATE": _NOON}
    out = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env
    )
    return out.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "checkout" / "vitreous-main"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "me@example.com")
    _git(root, "config", "user.name", "Me")
    _git(root, "remote", "add", "origin", "git@github.com:acme/vitreous.git")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "Fix sidecar slider drag")
    return root


def _cfg(tmp_path: Path, **kw) -> DevlogConfig:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return DevlogConfig(
        repo_path=str(tmp_path / "repo").replace("\\", "/"),
        obsidian_vault=str(vault).replace("\\", "/"),
        **kw,
    )


def _digest(path: str, *, threads=None, tokens=(0, 0, 0), msgs=("fix the slider",)):
    return SessionDigest(
        session_id=f"s-{path}",
        project_path=path,
        source="claude_code",
        start_time=T0,
        end_time=T0 + timedelta(minutes=30),
        user_messages=list(msgs),
        active_minutes=30.0,
        tokens_in=tokens[0],
        tokens_out=tokens[1],
        tokens_cache_read=tokens[2],
        threads=list(threads or []),
    )


# ------------------------------------------------------------------ threads


def test_extract_threads_from_next_steps_section():
    recap = (
        "Done. Summary of changes:\n"
        "- added hubs\n\n"
        "## Next steps\n"
        "1. Wire **commit** links into day notes\n"
        "2. Add `project_aliases` to [config](http://x)\n"
        "   (continuation line)\n\n"
        "Let me know if you want more.\n"
        "- not a thread\n"
    )
    assert extract_threads(recap) == [
        "Wire commit links into day notes",
        "Add project_aliases to config",
    ]


def test_extract_threads_variants():
    assert extract_threads("**Follow-ups:**\n- a\n- b") == ["a", "b"]
    assert extract_threads("Remaining work:\n* only one") == ["only one"]
    assert extract_threads("Notes\n- [ ] ship it\n- [x] done already") == ["ship it"]
    assert extract_threads("No follow-up needed.\n- just a list") == []
    assert extract_threads("") == []


def test_claude_parser_keeps_last_recap_of_each_turn(tmp_path: Path):
    lines = [
        {"type": "user", "timestamp": "2026-08-13T09:00:00Z", "cwd": "/w/p",
         "message": {"content": "build it"}},
        {"type": "assistant", "timestamp": "2026-08-13T09:05:00Z",
         "message": {"content": [{"type": "text", "text": "Working on it"}]}},
        {"type": "assistant", "timestamp": "2026-08-13T09:10:00Z",
         "message": {"content": [{"type": "text", "text": "Built.\n\nNext steps:\n- add tests"}]}},
        {"type": "user", "timestamp": "2026-08-13T09:20:00Z",
         "message": {"content": "now docs"}},
        {"type": "assistant", "timestamp": "2026-08-13T09:30:00Z",
         "message": {"content": [{"type": "text", "text": "Docs done.\n\nTODO:\n- publish"}]}},
    ]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    session = parse_session_file(path)
    threads = [e.threads for e in session.events if e.threads]
    assert threads == [["add tests"], ["publish"]]


# ------------------------------------------------------------------ identity


def test_resolver_uses_git_root_and_remote(repo: Path):
    assert find_git_root(repo / "src") == repo
    assert read_remote_url(repo) == "git@github.com:acme/vitreous.git"
    assert repo_web_url("https://github.com/acme/vitreous.git") == "https://github.com/acme/vitreous"
    project = ProjectResolver().resolve(str(repo / "src"))
    # Named after the remote, not the checkout folder or the cwd subfolder.
    assert project.name == "vitreous"
    assert project.root == repo
    assert project.repo_url == "https://github.com/acme/vitreous"


def test_resolver_follows_worktree_to_main_config(repo: Path, tmp_path: Path):
    wt = tmp_path / "delete-post"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert ProjectResolver().resolve(str(wt)).name == "vitreous"


def test_resolver_aliases_and_home(tmp_path: Path):
    home = tmp_path / "home" / "shibi"
    home.mkdir(parents=True)
    resolver = ProjectResolver({"window": "devlog", "C:/Work/Old": "legacy"}, home=home)
    assert resolver.resolve(str(home)).name == "home"
    assert resolver.resolve("/nowhere/window").name == "devlog"
    assert resolver.resolve("C:\\Work\\Old").name == "legacy"
    assert resolver.resolve("/nowhere/Other").name == "Other"
    meta = parse_post_meta(
        date(2026, 8, 12),
        "# d\n\nToday I logged 5 active min across devlog, window. Tools: Read (1x).\n",
        resolver,
    )
    assert [p["slug"] for p in meta["projects"]] == ["devlog"]


def test_commits_for_day_are_authored_by_me_and_linked(repo: Path):
    today = COMMIT_DAY
    _git(repo, "-c", "user.email=someone@else.com", "commit", "-q", "--allow-empty", "-m",
         "Not mine")
    commits = ProjectResolver().commits(ProjectResolver().resolve(str(repo)), today)
    assert [c["subject"] for c in commits] == ["Fix sidecar slider drag"]
    assert commits[0]["url"].startswith("https://github.com/acme/vitreous/commit/")
    assert ProjectResolver().commits(ProjectResolver().resolve(str(repo)),
                                     today - timedelta(days=30)) == []


def test_config_round_trips_project_aliases(tmp_path: Path):
    path = tmp_path / "config.toml"
    cfg = DevlogConfig(project_aliases={"delete-post": "devlog", 'we"ird': "x"})
    save_config(cfg, path)
    assert load_config(path).project_aliases == {"delete-post": "devlog", 'we"ird': "x"}
    with pytest.raises(ValueError, match="project_aliases"):
        DevlogConfig(project_aliases={"a": ""}).validate()


# ------------------------------------------------------------------ vault


def test_day_note_has_commits_tokens_threads_and_repo(repo: Path, tmp_path: Path):
    cfg = _cfg(tmp_path)
    today = COMMIT_DAY
    digest = _digest(str(repo), threads=["Add tests for #12"], tokens=(1_200_000, 45_000, 3_000))
    meta = build_day_meta(today, [digest], f"# {today}\n\nDid it.\n")
    [project] = meta["projects"]
    assert project["slug"] == "vitreous"
    assert project["tokens"] == {"in": 1_200_000, "out": 45_000, "cache": 3_000}
    assert len(project["commits"]) == 1

    try_mirror_post(cfg, today, f"# {today}\n\nDid it.\n", [digest])
    root = tmp_path / "vault" / "DevLog"
    note = (root / f"{today}.md").read_text(encoding="utf-8")
    assert "- **Tokens:** 1.2M in · 45k out · 3k cached" in note
    assert "[`" in note and "Fix sidecar slider drag" in note
    assert "    - [ ] Add tests for \\#12" in note
    assert "open_threads: 1" in note and "commits: 1" in note
    assert f'month: "[[DevLog/Monthly/{today:%Y-%m}|{today:%Y-%m}]]"' in note

    hub = (root / "Projects" / "vitreous.md").read_text(encoding="utf-8")
    assert 'repo: "https://github.com/acme/vitreous"' in hub
    assert "## Open threads" in hub and "- [ ] Add tests for \\#12 · [[" in hub
    assert (root / "Monthly" / f"{today:%Y-%m}.md").is_file()
    assert "file.inFolder(\"DevLog\")" in (root / "DevLog.base").read_text(encoding="utf-8")


def test_ticking_a_thread_is_remembered_and_can_be_undone(tmp_path: Path):
    cfg = _cfg(tmp_path)
    day = date(2026, 8, 13)
    digest = _digest("/w/devlog", threads=["ship weekly notes", "write docs"])
    try_mirror_post(cfg, day, "# d\n\nx\n", [digest])
    root = tmp_path / "vault" / "DevLog"
    hub = root / "Projects" / "devlog.md"

    # Tick in the hub (Tasks plugin appends a done date).
    hub.write_text(hub.read_text(encoding="utf-8").replace(
        "- [ ] ship weekly notes", "- [x] ship weekly notes ✅ 2026-08-14"), encoding="utf-8")
    try_mirror_post(cfg, date(2026, 8, 14), "# e\n\ny\n", [_digest("/w/other")])
    assert thread_key("ship weekly notes") in load_done_threads(root)
    assert "ship weekly notes" not in hub.read_text(encoding="utf-8").split("## Timeline")[0]
    note = (root / "2026-08-13.md").read_text(encoding="utf-8")
    assert "    - [x] ship weekly notes" in note
    assert "open_threads: 1" in note

    # Untick in the day note: reopened everywhere.
    (root / "2026-08-13.md").write_text(
        note.replace("- [x] ship weekly notes", "- [ ] ship weekly notes"), encoding="utf-8")
    try_mirror_post(cfg, date(2026, 8, 14), "# e\n\ny\n", [_digest("/w/other")])
    assert thread_key("ship weekly notes") not in load_done_threads(root)
    assert "- [ ] ship weekly notes · [[" in hub.read_text(encoding="utf-8")


def test_user_checkboxes_outside_managed_block_are_ignored(tmp_path: Path):
    cfg = _cfg(tmp_path)
    day = date(2026, 8, 13)
    try_mirror_post(cfg, day, "# d\n\nx\n", [_digest("/w/devlog", threads=["ship it"])])
    note = tmp_path / "vault" / "DevLog" / "2026-08-13.md"
    note.write_text(note.read_text(encoding="utf-8") + "- [x] ship it\n", encoding="utf-8")
    try_mirror_post(cfg, day, "# d\n\nx\n", [_digest("/w/devlog", threads=["ship it"])])
    assert load_done_threads(tmp_path / "vault" / "DevLog") == set()


def test_base_file_is_written_once(tmp_path: Path):
    cfg = _cfg(tmp_path)
    try_mirror_post(cfg, date(2026, 8, 13), "# d\n\nx\n", [_digest("/w/devlog")])
    base = tmp_path / "vault" / "DevLog" / "DevLog.base"
    base.write_text("views: []\n", encoding="utf-8")
    try_mirror_post(cfg, date(2026, 8, 14), "# e\n\ny\n", [_digest("/w/devlog")])
    assert base.read_text(encoding="utf-8") == "views: []\n"
