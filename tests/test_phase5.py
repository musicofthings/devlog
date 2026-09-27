"""Phase 5: auditing published posts, clinical redaction presets, pipeline runs."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from devlog.audit import audit_text, cmd_audit, scrub_text
from devlog.config import DevlogConfig, load_config, save_config
from devlog.models import SessionDigest
from devlog.pipelines import (
    find_launch_dirs,
    nextflow_runs,
    parse_duration,
    snakemake_runs,
)
from devlog.privacy import configure_redaction, redact_sensitive_text
from devlog.projects import ProjectResolver
from tests.test_hardening import _git, _repo
from tests.test_remaining import _day, _vault

DAY = date(2026, 8, 13)

# The shapes of the leaky posts on the live site (2026-08-08, 08-16, 08-17, 08-20).
LEAK_0808 = ("Today I logged 40 active min across devlog. I worked on devlog: <mcp_meta_tools> "
             "You have access to MCP (Model Context Protocol) tools through `GetMcpTools` and "
             "`CallMcpTool`. Tools: Read (65x), Shell (47x), Grep (23x).")
LEAK_0816 = ("Today I logged 309 active min across Gurukul, vitreous. I worked on vitreous: "
             "Continue from where you left off; Gurukul: # AGENTS.md instructions for "
             "~\\OneDrive\\Documents\\Gurukul <INSTRUCTIONS> # Gurukul bootstrap Before acting, "
             "read `Dharma. Tools: Bash (195x), exec (147x), Edit (95x).")
MIXED = ("Today I logged 90 active min across atlas, devlog. I worked on devlog: "
         "<system-reminder> ctx; atlas: fix the loader. Shipped 2 commit(s). Tools: Edit (3x).")


@pytest.fixture(autouse=True)
def _reset_redaction():
    configure_redaction([])
    yield
    configure_redaction([])


# ------------------------------------------------------------------ audit


def test_audit_flags_harness_text_as_leaks_and_nudges_as_notes():
    findings = audit_text(LEAK_0816, login_names=[])
    kinds = {(f.kind, f.is_leak) for f in findings}
    assert ("harness", True) in kinds and ("local-path", False) in kinds
    assert ("low-signal", False) in kinds
    assert audit_text("Today I logged 5 active min across atlas. I worked on atlas: add "
                      "List<T> support to the <div> parser. Tools: Edit (1x).", []) == []


def test_scrub_drops_bad_clauses_and_keeps_good_ones():
    assert scrub_text(LEAK_0808, []) == ("Today I logged 40 active min across devlog. "
                                         "Tools: Read (65x), Shell (47x), Grep (23x).")
    assert scrub_text(LEAK_0816, []) == ("Today I logged 309 active min across Gurukul, "
                                         "vitreous. Tools: Bash (195x), exec (147x), Edit (95x).")
    assert scrub_text(MIXED, []) == ("Today I logged 90 active min across atlas, devlog. "
                                     "I worked on atlas: fix the loader. Shipped 2 commit(s). "
                                     "Tools: Edit (3x).")
    for text in (LEAK_0808, LEAK_0816, MIXED):
        assert not [f for f in audit_text(scrub_text(text, []), []) if f.is_leak]


def test_scrub_handles_prose_posts_paths_and_login_names():
    prose = ("Shipped the loader.\n\nThen <mcp_meta_tools> leaked in. Wrote tests in "
             "C:\\Users\\jdoe\\atlas for shibi's setup.")
    out = scrub_text(prose, ["shibi"])
    assert "mcp_meta_tools" not in out and "Shipped the loader." in out
    assert "jdoe" not in out and "[USER]" in out and "home's setup" in out
    finding = audit_text("across shibi, atlas", ["shibi"])[0]
    assert finding.kind == "user-name" and not finding.is_leak


def test_identifier_findings_are_masked_when_printed():
    configure_redaction([r"MRN\d{6}"])
    [finding] = audit_text("Reviewed MRN123456 today.", [])
    assert finding.kind == "identifier" and finding.is_leak
    assert "123456" not in finding.shown()


def _audit_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "site"
    (repo / "posts").mkdir(parents=True)
    (repo / "posts" / "2026-08-08.md").write_text(f"# 2026-08-08\n\n{LEAK_0808}\n", "utf-8")
    (repo / "posts" / "2026-08-09.md").write_text("# 2026-08-09\n\nClean day.\n", "utf-8")
    return repo


def test_cmd_audit_reports_and_fixes_without_committing(tmp_path: Path, capsys):
    repo = _audit_repo(tmp_path)
    missing = str(tmp_path / "none.toml")
    assert cmd_audit(["--repo", str(repo), "--config", missing]) == 1
    out = capsys.readouterr().out
    assert "2026-08-08  LEAK  harness" in out and "2 post(s) scanned: 1 with leaks" in out

    assert cmd_audit(["--repo", str(repo), "--config", missing, "--fix", "--no-commit"]) == 0
    post = (repo / "posts" / "2026-08-08.md").read_text(encoding="utf-8")
    assert post == ("# 2026-08-08\n\nToday I logged 40 active min across devlog. "
                    "Tools: Read (65x), Shell (47x), Grep (23x).\n")
    page = (repo / "docs" / "log" / "2026-08-08.html").read_text(encoding="utf-8")
    assert "mcp_meta_tools" not in page
    assert (repo / "posts" / "2026-08-09.md").read_text(encoding="utf-8").endswith("Clean day.\n")
    assert cmd_audit(["--repo", str(repo), "--config", missing]) == 0


def test_cmd_audit_fix_commits_and_pushes(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "posts" / "2026-08-08.md").write_text(f"# 2026-08-08\n\n{LEAK_0808}\n", "utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "leaky post")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "main")

    cfg_path = tmp_path / "c.toml"
    save_config(DevlogConfig(repo_path=str(repo)), cfg_path)
    assert cmd_audit(["--config", str(cfg_path), "--fix"]) == 0
    assert _git(repo, "log", "-1", "--format=%s").startswith("audit: scrub 1 post(s) (2026-08-08)")
    assert _git(remote, "log", "-1", "--format=%s", "main").startswith("audit: scrub")
    shown = _git(repo, "show", "HEAD:posts/2026-08-08.md")
    assert "mcp_meta_tools" not in shown


# ------------------------------------------------------------------ presets


def test_clinical_preset_redacts_identifiers_but_not_ordinary_numbers():
    configure_redaction([], ["clinical"])
    text = ("MRN: 00123456, medical record number 998877, DOB 03/14/1961, SSN 123-45-6789, "
            "call (555) 123-4567 or jane.doe@hospital.org")
    out = redact_sensitive_text(text)
    for secret in ("00123456", "998877", "03/14/1961", "123-45-6789", "123-4567", "jane.doe"):
        assert secret not in out
    ordinary = ("On 2026-08-13 at 09:15 I ran nf-core/sarek 3.4.0 on 96 samples "
                "(chr1:1234567-1234999), Read (65x), GRCh38 p14.")
    assert redact_sensitive_text(ordinary) == ordinary


def test_config_validates_and_round_trips_new_fields(tmp_path: Path):
    with pytest.raises(ValueError, match="redact_presets"):
        DevlogConfig(redact_presets=["hipaa"]).validate()
    with pytest.raises(ValueError, match="pipeline_dirs"):
        DevlogConfig(pipeline_dirs=[""]).validate()
    path = tmp_path / "c.toml"
    save_config(DevlogConfig(redact_presets=["mrn", "dob"],
                             pipeline_dirs=["D:\\scratch\\runs"]), path)
    cfg = load_config(path)
    assert cfg.redact_presets == ["mrn", "dob"] and cfg.pipeline_dirs == ["D:/scratch/runs"]
    from devlog.init_cmd import UNPROMPTED_FIELDS

    assert {"redact_presets", "pipeline_dirs"} <= set(UNPROMPTED_FIELDS)


# ------------------------------------------------------------------ pipeline runs


def test_parse_duration():
    assert parse_duration("1h 2m 30s") == pytest.approx(62.5)
    assert parse_duration("45.6s") == pytest.approx(0.76)
    assert parse_duration("1d 2h") == 1560
    assert parse_duration("350ms") == pytest.approx(350 / 60000)
    assert parse_duration("-") is None


def _nextflow_dir(root: Path) -> Path:
    (root / ".nextflow").mkdir(parents=True)
    rows = [
        ("2026-08-13 09:15:02", "1h 2m 3s", "happy_curie", "OK", "abc1234", "s1",
         "nextflow run nf-core/sarek -r 3.4.0 -profile docker --input s.csv --outdir out"),
        ("2026-08-13 14:00:00", "3m 12s", "sad_turing", "ERR", "abc1234", "s2",
         "nextflow run nf-core/rnaseq -profile singularity -resume"),
        ("2026-08-13 18:30:00", "-", "busy_hopper", "-", "-", "s3", "nextflow run main.nf"),
        ("2026-08-12 10:00:00", "5m", "old_run", "OK", "abc", "s0", "nextflow run main.nf"),
        ("garbage line",),
    ]
    (root / ".nextflow" / "history").write_text(
        "\n".join("\t".join(r) for r in rows) + "\n", encoding="utf-8")
    (root / ".nextflow.log").write_text(
        "Aug-13 14:00:01.000 [main] INFO  nextflow.cli.CmdRun - Launching `nf-core/rnaseq` "
        "[sad_turing] DSL2 - revision: 9f8e7d6 [3.14.0]\n"
        "Aug-13 14:03:10.000 [main] ERROR nextflow.cli.Launcher - ERROR ~ Error executing "
        "process > 'NFCORE_RNASEQ:STAR_ALIGN (S1)'\n",
        encoding="utf-8")
    return root


def test_nextflow_runs_from_history_and_log(tmp_path: Path):
    runs = {r.run_name: r for r in nextflow_runs(_nextflow_dir(tmp_path / "proj"))}
    assert set(runs) == {"happy_curie", "sad_turing", "busy_hopper", "old_run"}
    ok, err, live = runs["happy_curie"], runs["sad_turing"], runs["busy_hopper"]
    assert (ok.pipeline, ok.version, ok.profile, ok.status) == (
        "nf-core/sarek", "3.4.0", "docker", "success")
    assert ok.minutes == pytest.approx(62.05) and ok.error is None
    assert (err.pipeline, err.version, err.status) == ("nf-core/rnaseq", "3.14.0", "failed")
    assert err.error.startswith("Error executing process > 'NFCORE_RNASEQ:STAR_ALIGN")
    assert live.status == "running" and live.minutes is None and live.pipeline == "main.nf"
    assert ok.slug == "nf-core-sarek"


def _snakemake_log(root: Path, stamp: str, text: str, minutes: float) -> None:
    log_dir = root / ".snakemake" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{stamp}.123456.snakemake.log"
    path.write_text(text, encoding="utf-8")
    end = datetime.strptime(stamp, "%Y-%m-%dT%H%M%S") + timedelta(minutes=minutes)
    os.utime(path, (end.timestamp(), end.timestamp()))


def test_snakemake_runs_status_error_and_duration(tmp_path: Path):
    wf = tmp_path / "variant-calling"
    _snakemake_log(wf, "2026-08-13T091500", "Building DAG of jobs...\n12 of 12 steps (100%) done\n",
                   30)
    _snakemake_log(wf, "2026-08-13T110000",
                   "Building DAG of jobs...\nError in rule bwa_mem:\n    jobid: 3\n"
                   "Exiting because a job execution failed. Look above for error message\n", 5)
    _snakemake_log(wf, "2026-08-13T120000", "Building DAG of jobs...\n", 1)
    runs = snakemake_runs(wf)
    assert [r.status for r in runs] == ["success", "failed", "incomplete"]
    assert runs[0].pipeline == "variant-calling" and runs[0].minutes == pytest.approx(30)
    assert runs[1].error == "Error in rule bwa_mem" and runs[2].error is None


def test_find_launch_dirs_is_bounded_and_skips_work(tmp_path: Path):
    root = tmp_path / "proj"
    _nextflow_dir(root / "runs" / "batch1")
    _nextflow_dir(root / "work" / "ab")  # nextflow's task dirs: never walked
    _nextflow_dir(root / "a" / "b" / "c")  # deeper than two levels
    _snakemake_log(root, "2026-08-13T091500", "(100%) done", 1)
    assert find_launch_dirs(root) == [root, root / "runs" / "batch1"]


def test_resolver_runs_filters_day_tags_project_and_uses_pipeline_dirs(tmp_path: Path):
    home = tmp_path / "home"
    _nextflow_dir(home / "runs")  # home itself is never scanned...
    scratch = tmp_path / "scratch" / "cohort-a"
    _nextflow_dir(scratch)
    resolver = ProjectResolver(home=home, pipeline_dirs=[str(tmp_path / "scratch")])
    runs = resolver.runs([str(home)], DAY)
    assert [r["run_name"] for r in runs] == ["happy_curie", "sad_turing", "busy_hopper"]
    assert {r["project"] for r in runs} == {"cohort-a"}
    assert "launch_dir" not in runs[0] and runs[0]["start"] == "2026-08-13T09:15"
    # ...unless you list a folder under it in pipeline_dirs.
    listed = ProjectResolver(home=home, pipeline_dirs=[str(home / "runs")])
    assert len(listed.runs([], DAY)) == 3


def _digest(path: Path, files=()) -> SessionDigest:
    t0 = datetime(2026, 8, 13, 9).astimezone()
    return SessionDigest(session_id="s", project_path=str(path), source="claude_code",
                         start_time=t0, end_time=t0 + timedelta(minutes=40),
                         active_minutes=40.0, user_messages=["run the sarek pipeline"],
                         files_touched=set(files))


def test_day_meta_links_runs_threads_and_notebooks(tmp_path: Path):
    from devlog.knowledge import build_day_meta

    proj = tmp_path / "atlas"
    _nextflow_dir(proj / "runs")
    nb = proj / "notebooks" / "qc.ipynb"
    meta = build_day_meta(DAY, [_digest(proj, [str(nb), str(proj / "a.py")])],
                          "# 2026-08-13\n\nx", ProjectResolver(home=tmp_path / "home"))
    assert [r["status"] for r in meta["runs"]] == ["success", "failed", "running"]
    assert {r["project_slug"] for r in meta["runs"]} == {"atlas"}
    [project] = meta["projects"]
    assert any(t.startswith("Fix failed nextflow run nf-core/rnaseq `sad_turing`: Error executing")
               for t in project["threads"])
    assert project["notebooks"] == [{"name": "qc.ipynb", "path": str(nb).replace("\\", "/")}]
    assert meta["threads"] == []


def _run(status: str, *, project: str = "atlas", hour: int = 9, error=None) -> dict:
    return {"engine": "nextflow", "pipeline": "nf-core/sarek", "slug": "nf-core-sarek",
            "status": status, "start": f"2026-08-13T{hour:02d}:15", "minutes": 62.0,
            "run_name": f"run_{hour}", "version": "3.4.0", "profile": "docker",
            "error": error, "project_slug": project}


def test_vault_renders_runs_pipeline_hub_and_day_level_threads(tmp_path: Path):
    from devlog.obsidian import refresh_vault

    day = _day("2026-08-13", "atlas", ["run sarek"])
    day["projects"][0]["notebooks"] = [{"name": "qc.ipynb", "path": "C:/w/atlas/qc.ipynb"}]
    day["runs"] = [_run("success"), _run("failed", project="cohort-a", hour=14, error="boom")]
    day["threads"] = ["Fix failed nextflow run nf-core/sarek `run_14`: boom"]
    cfg, root = _vault(tmp_path, {"2026-08-13": day})
    refresh_vault(cfg)

    note = (root / "2026-08-13.md").read_text(encoding="utf-8")
    assert "pipeline_runs: 2" in note and "failed_runs: 1" in note
    assert '  - "[[DevLog/Pipelines/nf-core-sarek|nf-core/sarek]]"' in note
    assert "devlog/pipeline/nf-core-sarek" in note
    assert ("- ✅ [[DevLog/Pipelines/nf-core-sarek|nf-core/sarek]] 3.4.0 · `run_9` · 09:15 · "
            "62 min · docker · [[DevLog/Projects/atlas|atlas]]") in note
    assert "- ❌ [[DevLog/Pipelines/nf-core-sarek|nf-core/sarek]] 3.4.0 · `run_14`" in note
    assert "· cohort-a — boom" in note  # no hub for a project without sessions
    assert "- [ ] Fix failed nextflow run nf-core/sarek `run_14`: boom" in note
    assert "open_threads: 1" in note
    assert "[qc.ipynb](<file:///C:/w/atlas/qc.ipynb>)" in note

    hub = (root / "Pipelines" / "nf-core-sarek.md").read_text(encoding="utf-8")
    assert "type: devlog-pipeline" in hub and "runs: 2" in hub and "failed: 1" in hub
    assert "| [[DevLog/2026-08-13\\|2026-08-13]] 14:15 | `run_14` | 3.4.0 | ❌ failed |" in hub
    assert "## Recent failures" in hub and "boom" in hub
    project_hub = (root / "Projects" / "atlas.md").read_text(encoding="utf-8")
    assert "## Pipeline runs" in project_hub and "`run_9` · 09:15 · 62 min · docker · " \
        "[[DevLog/2026-08-13|2026-08-13]]" in project_hub
    home = (root / "DevLog Home.md").read_text(encoding="utf-8")
    assert "## Pipelines" in home and "[[DevLog/Pipelines/nf-core-sarek|nf-core/sarek]] ×2" in home

    # Ticking the day-level thread closes it on the next refresh.
    (root / "2026-08-13.md").write_text(note.replace("- [ ] Fix failed", "- [x] Fix failed"),
                                        encoding="utf-8")
    refresh_vault(cfg)
    assert "- [x] Fix failed" in (root / "2026-08-13.md").read_text(encoding="utf-8")


def test_backfilled_days_pick_up_runs_from_pipeline_dirs(tmp_path: Path):
    from devlog.knowledge import parse_post_meta

    _nextflow_dir(tmp_path / "scratch" / "cohort-a")
    resolver = ProjectResolver(home=tmp_path / "home", pipeline_dirs=[str(tmp_path / "scratch")])
    meta = parse_post_meta(DAY, "# 2026-08-13\n\nToday I logged 30 active min across atlas. "
                                "Tools: Edit (2x).", resolver)
    assert len(meta["runs"]) == 3 and len(meta["threads"]) == 1


def test_git_is_not_needed_for_runs(tmp_path: Path):
    # A launch dir outside any git repo is named after its folder.
    _nextflow_dir(tmp_path / "loose")
    runs = ProjectResolver(home=tmp_path / "home").runs([str(tmp_path / "loose")], DAY)
    assert {r["project"] for r in runs} == {"loose"}


def test_cmd_audit_fix_rolls_back_when_push_fails(tmp_path: Path, capsys):
    repo = _repo(tmp_path)  # no remote: the push fails after the local commit
    leaky = f"# 2026-08-08\n\n{LEAK_0808}\n"
    (repo / "posts" / "2026-08-08.md").write_text(leaky, "utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "leaky post")
    head = _git(repo, "rev-parse", "HEAD")
    cfg_path = tmp_path / "c.toml"
    save_config(DevlogConfig(repo_path=str(repo)), cfg_path)
    assert cmd_audit(["--config", str(cfg_path), "--fix"]) == 1
    assert "Audit fix failed" in capsys.readouterr().out
    assert _git(repo, "rev-parse", "HEAD") == head
    assert (repo / "posts" / "2026-08-08.md").read_text(encoding="utf-8") == leaky
