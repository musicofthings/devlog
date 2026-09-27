"""Phase 3: topics, related days, streaks/review, canvases, agent memory (MCP)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devlog.config import DevlogConfig, load_config, save_config
from devlog.memory import VaultMemory
from devlog.obsidian import refresh_vault
from devlog.related import Corpus, tokenize
from devlog.topics import TopicDetector
from devlog.vault_graph import annotate, save_index, streaks


def _project(slug: str, tasks: list[str], *, minutes: int = 30, threads=(), files=(),
             commits=()) -> dict:
    return {
        "slug": slug, "name": slug, "minutes": minutes, "sessions": 1, "sources": ["codex"],
        "work_types": ["feature"], "tasks": list(tasks), "files": list(files),
        "tools": {}, "threads": list(threads),
        "commits": [{"short": c[:7], "sha": c, "subject": c, "url": None} for c in commits],
    }


def _day(d: str, *projects: dict) -> dict:
    return {
        "date": d, "active_minutes": sum(p["minutes"] for p in projects),
        "sessions": len(projects), "sources": ["codex"], "work_types": ["feature"],
        "projects": list(projects), "summary": f"Worked on {d}.", "origin": "sessions",
    }


def _quiet(d: str) -> dict:
    return {"date": d, "active_minutes": 0, "sessions": 0, "sources": [], "work_types": [],
            "projects": [], "summary": "No coding activity logged today.", "origin": "post"}


DAYS = {
    "2026-08-10": _day("2026-08-10", _project("atlas", ["run scanpy QC on the h5ad atlas"],
                                              files=["qc.py"])),
    "2026-08-11": _day("2026-08-11", _project("atlas", ["cluster with leiden in scanpy"],
                                              threads=["compare to Seurat clusters"])),
    "2026-08-12": _quiet("2026-08-12"),
    "2026-08-13": _day("2026-08-13", _project("devlog", ["add obsidian hubs"],
                                              commits=["Add hubs for obsidian"])),
    "2026-08-18": _day("2026-08-18", _project("pipeline",
                                              ["port GATK HaplotypeCaller step to Nextflow"])),
}


def _vault_cfg(tmp_path: Path, days: dict | None = None, **kw) -> DevlogConfig:
    vault = tmp_path / "vault"
    root = vault / "DevLog"
    root.mkdir(parents=True)
    for d in days or DAYS:
        (root / f"{d}.md").write_text("", encoding="utf-8")
    save_index(root, dict(days or DAYS))
    return DevlogConfig(obsidian_vault=str(vault).replace("\\", "/"), **kw)


# ------------------------------------------------------------------ topics


def test_topic_detector_catalog_custom_and_false_positives():
    d = TopicDetector({"CRISPR screens": ["crispr", "mageck"]})
    assert d.detect(["run scanpy QC", "edit app.py and plot.R", "MAGeCK count"]) == [
        "crispr-screens", "python", "r-lang", "scanpy"]
    assert d.detect(["git sync and code review", "we need to react to this"]) == []
    assert d.names["crispr-screens"] == "CRISPR screens"
    assert d.categories["gatk"] == "bioinformatics"


def test_config_round_trips_topics(tmp_path: Path):
    path = tmp_path / "c.toml"
    save_config(DevlogConfig(topics={"CRISPR screens": ["crispr", "sgRNA"]}), path)
    assert load_config(path).topics == {"CRISPR screens": ["crispr", "sgRNA"]}
    with pytest.raises(ValueError, match="topics"):
        DevlogConfig(topics={"x": "not-a-list"}).validate()


# ------------------------------------------------------------------ related


def test_tokenize_drops_stopwords_and_numbers():
    assert tokenize("Run the scanpy QC on 2026 h5ad files") == ["scanpy", "h5ad"]


def test_corpus_similarity_and_search_explain_themselves():
    corpus = Corpus({
        "a": "scanpy leiden clustering atlas",
        "b": "scanpy qc atlas h5ad",
        "c": "obsidian hubs vault",
    })
    [(other, score, terms)] = corpus.similar("a", k=1)
    assert other == "b" and score > 0 and set(terms) <= {"scanpy", "atlas"}
    assert corpus.similar("c", k=3) == []
    assert corpus.search("obsidian")[0][0] == "c"
    assert corpus.search("zzz") == []


def test_annotate_adds_topics_and_related_without_mutating_input():
    snapshot = json.dumps(DAYS, sort_keys=True)
    out = annotate(DAYS, TopicDetector())
    assert json.dumps(DAYS, sort_keys=True) == snapshot
    assert {"scanpy", "python"} <= set(out["2026-08-10"]["topics"])
    assert out["2026-08-18"]["projects"][0]["topics"] == ["gatk", "nextflow"]
    related = out["2026-08-10"]["related"]
    assert related[0]["date"] == "2026-08-11"
    assert related[0]["projects"] == ["atlas"]
    assert "related" not in out["2026-08-12"]


def test_streaks():
    assert streaks({}) == (0, 0)
    assert streaks(DAYS) == (1, 2)  # 08-18 alone now; 08-10..11 is the longest run
    assert streaks({**DAYS, "2026-08-19": _quiet("2026-08-19")}) == (0, 2)


# ------------------------------------------------------------------ vault


def test_refresh_writes_topic_hubs_related_days_review_and_home(tmp_path: Path):
    cfg = _vault_cfg(tmp_path)
    assert refresh_vault(cfg)["status"] == "refreshed"
    root = tmp_path / "vault" / "DevLog"

    day = (root / "2026-08-10.md").read_text(encoding="utf-8")
    assert '  - "[[DevLog/Topics/scanpy|scanpy]]"' in day
    assert "  - devlog/topic/scanpy" in day
    assert "## Related days" in day and "[[DevLog/2026-08-11|2026-08-11]]" in day

    hub = (root / "Topics" / "scanpy.md").read_text(encoding="utf-8")
    assert "type: devlog-topic" in hub and 'category: "single-cell"' in hub
    assert "[[DevLog/Projects/atlas|atlas]] ×2" in hub
    assert "## Literature & notes" in hub

    week = (root / "Weekly" / "2026-W34.md").read_text(encoding="utf-8")
    assert "## Review" in week
    assert "30 min vs 90 in [[DevLog/Weekly/2026-W33|2026-W33]] (-67%)" in week
    assert "**New projects:** [[DevLog/Projects/pipeline|pipeline]]" in week
    assert "**First time:**" in week and "[[DevLog/Topics/gatk|GATK]]" in week

    home = (root / "DevLog Home.md").read_text(encoding="utf-8")
    assert "current_streak: 1" in home and "longest_streak: 2" in home
    assert "## Topics" in home
    assert "renderHeatmapCalendar" in home and "dv.pages('\"DevLog\"')" in home


def test_topic_hub_literature_notes_survive_and_new_custom_topic_applies(tmp_path: Path):
    cfg = _vault_cfg(tmp_path)
    refresh_vault(cfg)
    hub = tmp_path / "vault" / "DevLog" / "Topics" / "scanpy.md"
    hub.write_text(hub.read_text(encoding="utf-8") + "- [[@wolf2018]] Scanpy paper\n",
                   encoding="utf-8")
    cfg.topics = {"Leiden clustering": ["leiden"]}
    refresh_vault(cfg)
    assert "[[@wolf2018]] Scanpy paper" in hub.read_text(encoding="utf-8")
    assert (tmp_path / "vault" / "DevLog" / "Topics" / "leiden-clustering.md").is_file()


def test_canvas_is_valid_regenerated_and_left_alone_once_edited(tmp_path: Path):
    cfg = _vault_cfg(tmp_path)
    refresh_vault(cfg)
    root = tmp_path / "vault" / "DevLog"
    canvas = root / "Canvas" / "atlas.canvas"
    data = json.loads(canvas.read_text(encoding="utf-8"))
    files = [n["file"] for n in data["nodes"] if n["type"] == "file"]
    assert files[:3] == ["DevLog/Projects/atlas.md", "DevLog/2026-08-10.md",
                         "DevLog/2026-08-11.md"]
    assert all((tmp_path / "vault" / f).is_file() for f in files)
    ids = {n["id"] for n in data["nodes"]}
    assert all(e["fromNode"] in ids and e["toNode"] in ids for e in data["edges"])
    assert "[[DevLog/Canvas/atlas.canvas|timeline canvas]]" in (
        root / "Projects" / "atlas.md").read_text(encoding="utf-8")

    # The user rearranges it: later refreshes must not clobber it.
    data["nodes"][0]["x"] = 999
    edited = json.dumps(data)
    canvas.write_text(edited, encoding="utf-8")
    cfg2 = cfg
    days = {**DAYS, "2026-08-19": _day("2026-08-19", _project("atlas", ["more scanpy"]))}
    (root / "2026-08-19.md").write_text("", encoding="utf-8")
    save_index(root, days)
    refresh_vault(cfg2)
    assert canvas.read_text(encoding="utf-8") == edited

    # Deleting it hands it back to devlog.
    canvas.unlink()
    refresh_vault(cfg2)
    assert "2026-08-19" in canvas.read_text(encoding="utf-8")


# ------------------------------------------------------------------ memory / MCP


def test_memory_queries(tmp_path: Path):
    cfg = _vault_cfg(tmp_path)
    refresh_vault(cfg)
    mem = VaultMemory(cfg)
    assert "| atlas | 2 | 60 | 1 | 2026-08-11 |" in mem.list_projects()
    status = mem.project_status("Atlas")
    assert "2 active day(s)" in status and "- [ ] compare to Seurat clusters" in status
    assert "scanpy" in status
    assert "compare to Seurat clusters (from 2026-08-11)" in mem.open_threads()
    assert mem.open_threads("nope") == "No project matching 'nope'."
    assert mem.search("nextflow gatk").startswith("- 2026-08-18")
    recent = mem.recent_activity(3)
    assert "2026-08-18" in recent and "2026-08-13" not in recent
    assert "2026-08-10" in mem.recent_activity(30, project="atlas")
    assert "commit Add hub: Add hubs for obsidian" in mem.day_log("2026-08-13")
    assert mem.day_log("1999-01-01") == "No devlog entry for 1999-01-01."


def test_memory_without_vault_is_graceful():
    mem = VaultMemory(DevlogConfig(obsidian_vault=""))
    assert mem.list_projects() == "No projects in the devlog vault yet."
    assert mem.recent_activity() == "The devlog vault is empty."


def test_mcp_server_registers_read_only_tools(tmp_path: Path):
    pytest.importorskip("mcp.server.mcpserver")
    import asyncio

    from devlog.mcp_server import build_server

    server = build_server(_vault_cfg(tmp_path))
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == {
        "list_projects", "recent_activity", "project_status", "open_threads",
        "search_log", "day_log"}
    assert all(t.annotations.read_only_hint for t in tools)


def test_cli_mcp_requires_vault(tmp_path: Path, capsys):
    from devlog.cli import main

    path = tmp_path / "c.toml"
    save_config(DevlogConfig(), path)
    assert main(["mcp", "--config", str(path)]) == 2
    assert "obsidian_vault" in capsys.readouterr().err
