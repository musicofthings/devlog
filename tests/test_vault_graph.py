"""Tests for the Obsidian knowledge graph: metadata, work types, hubs, noise."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from devlog.config import DevlogConfig
from devlog.digest import slice_for_date
from devlog.knowledge import build_day_meta, headline, parse_post_meta, project_slug
from devlog.models import RawSession, SessionDigest, SessionEvent
from devlog.noise import headline_task, is_injected_prompt, is_low_signal_prompt
from devlog.obsidian import archive_path, backfill_posts, remove_mirrored_post, try_mirror_post
from devlog.summarize import summarize_with_template
from devlog.vault_graph import END, load_index, refresh_graph, safe_text
from devlog.worktypes import classify

T0 = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)


def _cfg(tmp_path: Path) -> DevlogConfig:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return DevlogConfig(
        repo_path=str(tmp_path / "repo").replace("\\", "/"),
        obsidian_vault=str(vault).replace("\\", "/"),
    )


def _digest(project: str, messages: list[str], *, minutes: int = 30, source: str = "claude_code",
            tools: dict[str, int] | None = None, files: set[str] | None = None) -> SessionDigest:
    return SessionDigest(
        session_id=f"{project}-{minutes}",
        project_path=f"/work/{project}",
        source=source,
        start_time=T0,
        end_time=T0 + timedelta(minutes=minutes),
        user_messages=messages,
        tool_calls=tools or {},
        files_touched=files or set(),
        active_minutes=float(minutes),
    )


# ------------------------------------------------------------------ noise


def test_injected_prompts_are_detected():
    assert is_injected_prompt("<mcp_meta_tools> You have access to MCP tools")
    assert is_injected_prompt("# AGENTS.md instructions for ~/Gurukul <INSTRUCTIONS>")
    assert is_injected_prompt("Base directory for this skill: ~/.claude/plugins/x")
    assert not is_injected_prompt("git sync and code review")
    assert not is_injected_prompt("fix the <div> alignment")


def test_low_signal_prompts_and_headline():
    assert is_low_signal_prompt("Try again")
    assert is_low_signal_prompt("Continue from where you left off.")
    assert is_low_signal_prompt("resume session")
    assert not is_low_signal_prompt("continue the obsidian integration")
    assert headline_task(["Try again", "add weekly notes"]) == "add weekly notes"
    assert headline_task(["ok"]) == "ok"
    assert headline_task([]) is None


def test_digest_drops_injected_messages():
    raw = RawSession(
        session_id="s", project_path="/p/devlog", source="cursor",
        start_time=T0, end_time=T0 + timedelta(minutes=5),
        events=[
            SessionEvent(timestamp=T0, user_message="<mcp_meta_tools> You have access"),
            SessionEvent(timestamp=T0 + timedelta(minutes=5), user_message="wire obsidian links"),
        ],
    )
    [digest] = slice_for_date([raw], T0.date(), UTC)
    assert digest.user_messages == ["wire obsidian links"]


def test_template_prefers_informative_task():
    post = summarize_with_template(
        [_digest("vitreous", ["Try again", "fix the sidecar slider"])], detail="verbatim"
    )
    assert "vitreous: fix the sidecar slider" in post
    assert "Try again" not in post


# ------------------------------------------------------------------ work types


def test_classify_prompts_and_tool_fallback():
    assert classify(["git sync and code review"]) == ["code-review", "git-ops"]
    assert "planning" in classify(["review and suggest next phase of development"])
    assert "data-analysis" in classify(["run scanpy QC on the single-cell atlas"])
    assert classify(["hmm"], {"Edit": 4, "Read": 9}) == ["feature"]
    assert classify(["hmm"], {"Read": 9}) == ["code-review"]
    assert classify(["hmm"]) == []


# ------------------------------------------------------------------ metadata


def test_project_slug_merges_case_and_is_path_safe():
    assert project_slug("Gurukul") == project_slug("gurukul") == "gurukul"
    assert project_slug("My Repo: v2") == "my-repo-v2"
    assert project_slug("///") == "unknown"


def test_build_day_meta_groups_by_project():
    digests = [
        _digest("vitreous", ["Try again", "code review the renderer"], minutes=40,
                tools={"Read": 5}, files={"/work/vitreous/app.py"}),
        _digest("Vitreous", ["fix the slider bug"], minutes=20, source="cursor"),
        _digest("devlog", ["<mcp_meta_tools> junk", "add obsidian hubs"], minutes=10),
    ]
    meta = build_day_meta(date(2026, 8, 13), digests, "# 2026-08-13\n\nDid things.\n")
    assert meta["origin"] == "sessions"
    assert meta["summary"] == "Did things."
    assert [p["slug"] for p in meta["projects"]] == ["vitreous", "devlog"]
    vit = meta["projects"][0]
    assert vit["sessions"] == 2
    assert vit["sources"] == ["claude_code", "cursor"]
    assert vit["tasks"] == ["code review the renderer", "fix the slider bug"]
    assert vit["files"] == ["app.py"]
    assert {"code-review", "bugfix"} <= set(vit["work_types"])
    assert meta["projects"][1]["tasks"] == ["add obsidian hubs"]
    assert headline(meta) == "code review the renderer"


def test_parse_post_meta_from_template_post():
    post = (
        "# 2026-08-14\n\nToday I logged 187 active min across devlog, vitreous. "
        "I worked on vitreous: Try again; devlog: <mcp_meta_tools> You have access. "
        "Tools: Read (245x), StrReplace (182x)."
    )
    meta = parse_post_meta(date(2026, 8, 14), post)
    assert meta["active_minutes"] == 187
    assert [p["slug"] for p in meta["projects"]] == ["devlog", "vitreous"]
    assert all(p["tasks"] == [] for p in meta["projects"])
    # Multi-project: per-project minutes and tools are unknown, not guessed.
    assert all(p["minutes"] is None and p["tools"] == {} for p in meta["projects"])


def test_parse_post_meta_single_project_and_prose():
    single = parse_post_meta(
        date(2026, 9, 2),
        "# 2026-09-02\n\nToday I logged 54 active min across vitreous. I worked on vitreous: "
        "git pull and code review. Tools: Read (210x), Shell (77x).\n",
    )
    [vit] = single["projects"]
    assert vit["minutes"] == 54
    assert vit["tools"] == {"Read": 210, "Shell": 77}
    assert vit["work_types"] == ["code-review", "git-ops"]

    prose = parse_post_meta(date(2026, 7, 22), "# 2026-07-22\n\nBuilt the publish CLI.\n")
    assert prose["projects"] == []
    assert prose["work_types"] == ["feature"]

    empty = parse_post_meta(date(2026, 9, 1), "# 2026-09-01\n\nNo coding activity logged today.\n")
    assert empty["projects"] == [] and empty["work_types"] == []


# ------------------------------------------------------------------ vault graph


def test_mirror_links_day_to_project_work_week_and_home(tmp_path: Path):
    cfg = _cfg(tmp_path)
    day = date(2026, 8, 13)
    digests = [_digest("vitreous", ["code review the renderer"], minutes=40)]
    out = try_mirror_post(cfg, day, "# 2026-08-13\n\nReviewed vitreous.\n", digests)
    assert out["status"] == "written"

    root = tmp_path / "vault" / "DevLog"
    note = (root / "2026-08-13.md").read_text(encoding="utf-8")
    assert '  - "[[DevLog/Projects/vitreous|vitreous]]"' in note
    assert '  - "[[DevLog/Work/code-review|code-review]]"' in note
    assert 'week: "[[DevLog/Weekly/2026-W33|2026-W33]]"' in note
    assert "  - devlog/project/vitreous" in note
    assert "[[DevLog/DevLog Home|Home]]" in note

    project = (root / "Projects" / "vitreous.md").read_text(encoding="utf-8")
    assert "type: devlog-project" in project
    assert "[[DevLog/2026-08-13\\|2026-08-13]] | 40 |" in project
    assert (root / "Work" / "code-review.md").is_file()
    assert "[[DevLog/2026-08-13|2026-08-13]]" in (root / "Weekly" / "2026-W33.md").read_text(
        encoding="utf-8"
    )
    assert "[[DevLog/Projects/vitreous\\|vitreous]]" in (root / "DevLog Home.md").read_text(
        encoding="utf-8"
    )
    assert load_index(root)["2026-08-13"]["origin"] == "sessions"


def test_prev_next_nav_skips_quiet_days(tmp_path: Path):
    cfg = _cfg(tmp_path)
    posts = tmp_path / "repo" / "posts"
    posts.mkdir(parents=True)
    active = "Today I logged 5 active min across devlog. Tools: Read (1x)."
    (posts / "2026-08-10.md").write_text(f"# 2026-08-10\n\n{active}\n", encoding="utf-8")
    (posts / "2026-08-11.md").write_text(
        "# 2026-08-11\n\nNo coding activity logged today.\n", encoding="utf-8"
    )
    (posts / "2026-08-12.md").write_text(f"# 2026-08-12\n\n{active}\n", encoding="utf-8")
    assert backfill_posts(cfg, posts)["count"] == 3

    root = tmp_path / "vault" / "DevLog"
    first = (root / "2026-08-10.md").read_text(encoding="utf-8")
    assert "[[DevLog/2026-08-12|2026-08-12]] →" in first
    quiet = (root / "2026-08-11.md").read_text(encoding="utf-8")
    assert "← [[DevLog/2026-08-10|2026-08-10]]" in quiet
    assert "quiet day" in (root / "Weekly" / "2026-W33.md").read_text(encoding="utf-8")


def test_user_notes_survive_refresh_and_republish(tmp_path: Path):
    cfg = _cfg(tmp_path)
    day = date(2026, 8, 13)
    try_mirror_post(cfg, day, "# 2026-08-13\n\nFirst.\n", [_digest("devlog", ["add hubs"])])
    note = archive_path(cfg, day)
    note.write_text(note.read_text(encoding="utf-8") + "Idea: [[Graph thinking]]\n",
                    encoding="utf-8")
    hub = tmp_path / "vault" / "DevLog" / "Projects" / "devlog.md"
    hub.write_text(hub.read_text(encoding="utf-8") + "Roadmap lives here.\n", encoding="utf-8")

    try_mirror_post(cfg, day, "# 2026-08-13\n\nSecond.\n", [_digest("devlog", ["add hubs"])])
    text = note.read_text(encoding="utf-8")
    assert "Second." in text and "First." not in text
    assert text.count(END) == 1
    assert "Idea: [[Graph thinking]]" in text
    assert "Roadmap lives here." in hub.read_text(encoding="utf-8")


def test_backfill_keeps_session_detail_for_same_day(tmp_path: Path):
    cfg = _cfg(tmp_path)
    day = date(2026, 8, 13)
    body = "# 2026-08-13\n\nHand-edited summary.\n"
    try_mirror_post(cfg, day, body, [_digest("devlog", ["add hubs"], files={"/w/hub.py"})])
    posts = tmp_path / "repo" / "posts"
    posts.mkdir(parents=True)
    (posts / "2026-08-13.md").write_text(body, encoding="utf-8")
    backfill_posts(cfg, posts)
    meta = load_index(tmp_path / "vault" / "DevLog")["2026-08-13"]
    assert meta["origin"] == "sessions"
    assert meta["projects"][0]["files"] == ["hub.py"]


def test_remove_prunes_untouched_hubs_but_keeps_annotated(tmp_path: Path):
    cfg = _cfg(tmp_path)
    try_mirror_post(cfg, date(2026, 8, 13), "# a\n\nx\n", [_digest("alpha", ["add a"])])
    try_mirror_post(cfg, date(2026, 8, 14), "# b\n\ny\n", [_digest("beta", ["add b"])])
    projects = tmp_path / "vault" / "DevLog" / "Projects"
    beta = projects / "beta.md"
    beta.write_text(beta.read_text(encoding="utf-8") + "keep me\n", encoding="utf-8")

    remove_mirrored_post(cfg, date(2026, 8, 13))
    assert not (projects / "alpha.md").exists()
    remove_mirrored_post(cfg, date(2026, 8, 14))
    assert beta.exists() and "keep me" in beta.read_text(encoding="utf-8")


def test_manually_deleted_day_note_is_not_resurrected(tmp_path: Path):
    cfg = _cfg(tmp_path)
    try_mirror_post(cfg, date(2026, 8, 13), "# a\n\nx\n", [_digest("alpha", ["add a"])])
    archive_path(cfg, date(2026, 8, 13)).unlink()
    try_mirror_post(cfg, date(2026, 8, 14), "# b\n\ny\n", [_digest("beta", ["add b"])])
    assert not archive_path(cfg, date(2026, 8, 13)).exists()
    assert "2026-08-13" not in load_index(tmp_path / "vault" / "DevLog")


def test_refresh_is_idempotent(tmp_path: Path):
    cfg = _cfg(tmp_path)
    try_mirror_post(cfg, date(2026, 8, 13), "# a\n\nx\n", [_digest("alpha", ["add a"])])
    days = load_index(tmp_path / "vault" / "DevLog")
    assert refresh_graph(tmp_path / "vault", "DevLog", days)["written"] == []


def test_safe_text_neutralizes_markdown_side_effects():
    out = safe_text("fix #12 in [[Note]] <b>%% x | y", table=True)
    assert "[[" not in out and "%%" not in out and "<b>" not in out
    assert "\\#12" in out and "\\|" in out


def test_new_vault_gets_graph_colors_but_existing_settings_are_kept(tmp_path: Path):
    from devlog.obsidian import create_obsidian_vault

    vault = create_obsidian_vault(tmp_path / "fresh")
    assert "devlog/project-hub" in (vault / ".obsidian" / "graph.json").read_text(
        encoding="utf-8"
    )
    (vault / ".obsidian" / "graph.json").write_text("{}", encoding="utf-8")
    create_obsidian_vault(vault)
    assert (vault / ".obsidian" / "graph.json").read_text(encoding="utf-8") == "{}"


def test_upgrade_keeps_user_edits_in_pre_graph_day_notes(tmp_path: Path):
    cfg = _cfg(tmp_path)
    root = tmp_path / "vault" / "DevLog"
    root.mkdir(parents=True)
    legacy = "---\ndate: {d}\ntags:\n  - devlog\n---\n\n# {d}\n\nBuilt it.\n"
    (root / "2026-08-12.md").write_text(legacy.format(d="2026-08-12"), encoding="utf-8")
    edited = legacy.format(d="2026-08-13") + "\nMy retro: ship smaller.\n"
    (root / "2026-08-13.md").write_text(edited, encoding="utf-8")
    posts = tmp_path / "repo" / "posts"
    posts.mkdir(parents=True)
    for d in ("2026-08-12", "2026-08-13"):
        (posts / f"{d}.md").write_text(f"# {d}\n\nBuilt it.\n", encoding="utf-8")
    backfill_posts(cfg, posts)

    untouched = (root / "2026-08-12.md").read_text(encoding="utf-8")
    assert "Kept from the previous version" not in untouched
    kept = (root / "2026-08-13.md").read_text(encoding="utf-8")
    assert kept.count(END) == 1
    assert "My retro: ship smaller." in kept.split(END)[1]
