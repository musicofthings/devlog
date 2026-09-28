"""Phase 5, items 5-6: MCP write tools, devlog doctor, RSS + search, packaging."""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from devlog.config import DevlogConfig, save_config
from devlog.doctor import FAIL, OK, WARN, format_checks, run_checks
from devlog.site import build_feed_html, build_rss, rebuild_site, site_base_url
from devlog.status import record_event
from devlog.vault_write import VaultWriter, clean_agent_text
from tests.test_remaining import _day, _vault

NOW = datetime(2026, 9, 22, 14, 5)


def _days() -> dict[str, dict]:
    a = _day("2026-09-21", "atlas", ["cluster PBMCs"],
             threads=["Check the doublet rate", "Rerun QC with new thresholds"])
    b = _day("2026-09-22", "atlas", ["write methods"], threads=["Check the doublet rate"])
    b["threads"] = ["Fix failed nextflow run nf-core/scrnaseq `r1`: STAR ran out of memory"]
    return {"2026-09-21": a, "2026-09-22": b}


def _writer(tmp_path: Path, **cfg) -> tuple[VaultWriter, Path]:
    from devlog.obsidian import refresh_vault

    config, root = _vault(tmp_path, _days(), zotero_url="", **cfg)
    refresh_vault(config)
    return VaultWriter(config, now=lambda: NOW), root


# ------------------------------------------------------------------ close_thread


def test_close_thread_ticks_it_everywhere_and_records_it(tmp_path: Path):
    from devlog.vault_graph import load_state

    writer, root = _writer(tmp_path)
    assert writer.close_thread("doublet rate") == "Closed: Check the doublet rate (2026-09-22)"
    for note in (root / "2026-09-21.md", root / "2026-09-22.md"):
        text = note.read_text(encoding="utf-8")
        assert "- [x] Check the doublet rate" in text
        assert "- [ ] Check the doublet rate" not in text
    hub = (root / "Projects" / "atlas.md").read_text(encoding="utf-8")
    assert "doublet" not in hub.split("## Open threads")[1].split("##")[0]
    assert "check the doublet rate" in load_state(root)["done_threads"]
    log = [json.loads(line) for line in
           (root / ".devlog" / "agent-writes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[0]["tool"] == "close_thread" and log[0]["note"] == "DevLog/2026-09-22.md"
    # Already done: nothing left to match.
    assert writer.close_thread("doublet rate").startswith("No open thread matches")


def test_close_thread_disambiguates_and_handles_day_level_threads(tmp_path: Path):
    writer, root = _writer(tmp_path)
    ambiguous = writer.close_thread("r")  # matches several threads
    assert "open threads match; quote one exactly" in ambiguous
    assert writer.close_thread("QC", project="nope") == "No project matching 'nope'."
    assert writer.close_thread("STAR ran out of memory").startswith("Closed: Fix failed")
    assert "- [x] Fix failed nextflow run" in (root / "2026-09-22.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------ add_note / log_decision


def test_add_note_goes_below_the_managed_block_and_survives_refresh(tmp_path: Path):
    from devlog.obsidian import refresh_vault

    writer, root = _writer(tmp_path)
    assert writer.add_note("Doublets are high in batch 3.\n%% devlog:end %% sneaky") == \
        "Added a note to DevLog/2026-09-22.md."
    assert writer.add_note("Use scDblFinder next time", project="atlas") == \
        "Added a note to DevLog/Projects/atlas.md."
    missing = writer.add_note("x", day="2026-09-30")
    assert "No day note for 2026-09-30" in missing and "2026-09-22" in missing
    assert writer.add_note("hub fallback", day="2026-09-30", project="atlas").endswith(
        "DevLog/Projects/atlas.md.")

    refresh_vault(writer.cfg)  # regeneration keeps the user-owned tail
    day = (root / "2026-09-22.md").read_text(encoding="utf-8")
    tail = day.split("%% devlog:end %%", 1)[1]
    assert day.count("%% devlog:end %%") == 1  # the fake marker was neutralized
    assert "## Notes\n\n- 2026-09-22 14:05 (agent) Doublets are high in batch 3.\n" \
           "  sneaky\n" in tail  # the fake marker is dropped
    hub_tail = (root / "Projects" / "atlas.md").read_text(encoding="utf-8").split(
        "%% devlog:end %%", 1)[1]
    assert hub_tail.index("scDblFinder") < hub_tail.index("hub fallback")


def test_log_decision_appends_separate_callouts(tmp_path: Path):
    from devlog.obsidian import refresh_vault

    writer, root = _writer(tmp_path)
    assert writer.log_decision("atlas", "Doublet caller", "Use scDblFinder",
                               why="Better on 10x v3").startswith("Logged decision")
    writer.log_decision("atlas", "Clustering", "Leiden at resolution 0.8")
    refresh_vault(writer.cfg)
    tail = (root / "Projects" / "atlas.md").read_text(encoding="utf-8").split(
        "%% devlog:end %%", 1)[1]
    assert ("## Decisions\n\n> [!decision] 2026-09-22 · Doublet caller\n"
            "> **Decision:** Use scDblFinder\n>\n> **Why:** Better on 10x v3\n\n"
            "> [!decision] 2026-09-22 · Clustering\n") in tail
    assert writer.log_decision("nope", "t", "d") == "No project hub matching 'nope'."
    assert writer.log_decision("atlas", "", "d").startswith("A decision needs")


def test_clean_agent_text():
    assert clean_agent_text("  a\r\nb%%c  ") == "a\nb%c"
    assert clean_agent_text("x" * 3000).endswith("…")
    assert len(clean_agent_text("x" * 3000)) == 2000


def test_mcp_write_tools_are_opt_in(tmp_path: Path):
    pytest.importorskip("mcp.server.mcpserver")
    import asyncio

    from devlog.mcp_server import build_server

    cfg, _ = _vault(tmp_path, _days())
    names = {t.name for t in asyncio.run(build_server(cfg).list_tools())}
    assert "close_thread" not in names
    cfg.mcp_write = True
    tools = {t.name: t for t in asyncio.run(build_server(cfg).list_tools())}
    assert {"close_thread", "add_note", "log_decision"} <= set(tools)
    assert tools["add_note"].annotations.read_only_hint is False
    assert tools["add_note"].annotations.destructive_hint is False
    with pytest.raises(ValueError, match="mcp_write"):
        DevlogConfig(mcp_write="yes").validate()  # type: ignore[arg-type]


# ------------------------------------------------------------------ doctor


def _fake_run(responses: dict[str, tuple[int, str]]):
    def run(cmd, cwd):
        key = " ".join(cmd[:3])
        code, out = responses.get(key, (1, ""))
        return subprocess.CompletedProcess(cmd, code, out, "")
    return run


def _setup(tmp_path: Path, **cfg) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "posts").mkdir()
    (repo / "posts" / "2026-09-22.md").write_text("# x\n\nToday I logged 5 active min.\n", "utf-8")
    claude = tmp_path / "claude"
    claude.mkdir()
    path = tmp_path / "c.toml"
    save_config(DevlogConfig(repo_path=str(repo), claude_root=str(claude),
                             sources=["claude_code", "codex"],
                             codex_root=str(tmp_path / "no-codex"), zotero_url="", **cfg), path)
    return path


GIT_OK = {"git remote get-url": (0, "https://github.com/me/site"),
          "git config user.email": (0, "me@x.org"), "gh auth status": (0, "")}


def test_doctor_reports_problems_warnings_and_hints(tmp_path: Path):
    checks = run_checks(tmp_path / "missing.toml")
    assert checks[-1].level == FAIL and "devlog init" in checks[-1].hint

    path = _setup(tmp_path, post_writer="ollama", public_detail="verbatim")
    checks = run_checks(path, run=_fake_run(GIT_OK), which=lambda _: "/usr/bin/gh",
                        ollama_models=lambda _: None, os_name="linux")
    by = {(c.area, c.level) for c in checks}
    assert ("sources", OK) in by and ("sources", WARN) in by  # claude found, codex missing
    assert ("repo", OK) in by and ("gh", OK) in by and ("audit", OK) in by
    assert ("privacy", WARN) in by  # verbatim with no redaction
    assert ("ollama", FAIL) in by  # the post writer depends on it
    assert ("vault", OK) in by  # no vault configured: the mirror is off, not a problem
    assert ("schedule", WARN) in by  # linux, no cron entry
    report = format_checks(checks)
    assert "[FAIL] ollama" in report and report.rstrip().endswith("warning(s).")
    assert report.isascii() or "→" not in report  # markers stay ASCII


def test_doctor_catches_leaks_missing_models_and_the_scheduled_task(tmp_path: Path):
    path = _setup(tmp_path, related_backend="ollama", publish_mode="auto")
    (tmp_path / "repo" / "posts" / "2026-09-23.md").write_text(
        "# x\n\nI worked on a: <mcp_meta_tools> leak.\n", "utf-8")
    checks = run_checks(path, run=_fake_run({**GIT_OK, "schtasks /Query /TN": (1, "")}),
                        which=lambda _: None, ollama_models=lambda _: ["llama3.2:latest"],
                        os_name="win32", now=datetime(2026, 9, 27, tzinfo=UTC))
    got = {c.area: c for c in checks}
    assert got["audit"].level == FAIL and "2026-09-23" in got["audit"].message
    assert got["ollama"].level == WARN and "nomic-embed-text" in got["ollama"].hint
    assert got["schedule"].level == FAIL and "devlog init" in got["schedule"].hint
    assert got["gh"].level == OK  # optional unless publish_mode = "pr"


@pytest.mark.parametrize(
    ("published_at", "mode", "level"),
    [
        (None, "auto", WARN),  # never published
        ("2026-09-26T06:30:00+00:00", "auto", OK),  # yesterday
        ("2026-09-20T06:30:00+00:00", "auto", WARN),  # stalled: check the scheduled task
        ("2026-09-20T06:30:00+00:00", "manual", OK),  # manual publishing is irregular by design
    ],
)
def test_doctor_reads_last_publish_from_status_file(tmp_path: Path, published_at, mode, level):
    path = _setup(tmp_path, publish_mode=mode)
    if published_at:
        # Written by the same helper publish uses, so the key names can't drift.
        record_event(tmp_path / "repo", event="published", date=published_at[:10], at=published_at)
    checks = run_checks(path, run=_fake_run(GIT_OK), which=lambda _: "/usr/bin/gh",
                        os_name="linux", now=datetime(2026, 9, 27, 12, tzinfo=UTC))
    publish = next(c for c in checks if c.area == "publish")
    assert publish.level == level
    if published_at:
        assert publish.message.startswith(f"last publish {published_at[:16]}")
    else:
        assert publish.message == "nothing published yet"


def test_cli_version_and_doctor(tmp_path: Path, capsys):
    from devlog import __version__
    from devlog.cli import main

    assert main(["--version"]) == 0 and capsys.readouterr().out == f"devlog {__version__}\n"
    assert main(["doctor", "--config", str(tmp_path / "none.toml")]) == 1


# ------------------------------------------------------------------ site: RSS + search


def test_site_base_url(tmp_path: Path):
    assert site_base_url(tmp_path, "Me/devlog") == "https://me.github.io/devlog/"
    assert site_base_url(tmp_path, "me/me.github.io") == "https://me.github.io/"
    assert site_base_url(tmp_path, None) is None
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "CNAME").write_text("log.example.org\n", encoding="utf-8")
    assert site_base_url(tmp_path, "me/devlog") == "https://log.example.org/"


def test_rss_is_valid_and_limited(tmp_path: Path):
    posts = [(date(2026, 9, d), tmp_path / f"{d}.md", f"# 2026-09-{d:02d}\n\nDay {d} <b>& co</b>")
             for d in range(30, 0, -1)] + [(date(2026, 8, 1), tmp_path / "x.md", "# x\n\nold")]
    root = ET.fromstring(build_rss(posts, "https://me.github.io/devlog/"))
    channel = root.find("channel")
    items = channel.findall("item")
    assert len(items) == 30 and items[0].find("title").text == "2026-09-30"
    assert items[0].find("link").text == "https://me.github.io/devlog/log/2026-09-30.html"
    assert items[0].find("description").text == "Day 30 <b>& co</b>"
    assert items[0].find("pubDate").text == "Wed, 30 Sep 2026 23:00:00 +0000"


def test_feed_page_has_search_and_rss_link(tmp_path: Path):
    html = build_feed_html([(date(2026, 9, 22), tmp_path / "a.md",
                             "# x\n\nTried Scanpy \"leiden\" clustering")])
    assert 'data-text="2026-09-22 tried scanpy &quot;leiden&quot; clustering"' in html
    assert 'id="log-search"' in html and '<a href="feed.xml">RSS</a>' in html
    assert 'rel="alternate" type="application/rss+xml"' in html
    assert 'id="log-search"' not in build_feed_html([])


def test_rebuild_site_writes_feed_only_with_a_known_url(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "posts").mkdir(parents=True)
    (repo / "posts" / "2026-09-22.md").write_text("# x\n\nHello\n", encoding="utf-8")
    rebuild_site(repo)  # no git remote, no CNAME: no absolute URL to publish
    assert not (repo / "docs" / "log" / "feed.xml").exists()
    (repo / "docs" / "CNAME").write_text("log.example.org", encoding="utf-8")
    written = rebuild_site(repo)
    feed = repo / "docs" / "log" / "feed.xml"
    assert feed in written and "https://log.example.org/log/2026-09-22.html" in \
        feed.read_text(encoding="utf-8")


def test_package_metadata():
    import tomllib

    from devlog import __version__

    data = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml")
                         .read_text(encoding="utf-8"))
    assert data["project"]["name"] == "daily-devlog"
    assert data["project"]["license"] == "MIT"
    assert (Path(__file__).resolve().parents[1] / "LICENSE").read_text(
        encoding="utf-8").startswith("MIT License")
    assert data["project"]["scripts"] == {"devlog": "devlog.cli:main"}
    assert data["tool"]["setuptools"]["packages"]["find"]["include"] == ["devlog*"]
    assert __version__.count(".") == 2
