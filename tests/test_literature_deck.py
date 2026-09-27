"""Phase 5, items 3-4: literature links (+ Zotero citekeys) and `devlog deck`."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from devlog.deck import build_deck, cmd_deck, resolve_scope
from devlog.literature import (
    BetterBibTeX,
    CitekeyResolver,
    Reference,
    find_references,
)
from devlog.privacy import configure_redaction
from tests.test_remaining import _day, _vault

DOI = "10.1038/s41586-020-2649-2"


@pytest.fixture(autouse=True)
def _reset_redaction():
    configure_redaction([])
    yield
    configure_redaction([])


# ------------------------------------------------------------------ finding references


def test_find_references_in_the_forms_people_paste():
    text = (f"compare with https://doi.org/{DOI}. Also (doi:10.1101/2023.05.01.538912), "
            "PMID: 31178118, https://pubmed.ncbi.nlm.nih.gov/34017140/ and PMC8457713; "
            "arXiv:2401.01234v2, https://arxiv.org/abs/2310.06825 and "
            "https://doi.org/10.48550/arXiv.2106.09685?utm=x")
    refs = [(r.kind, r.id) for r in find_references([text])]
    assert refs == [
        ("doi", DOI), ("doi", "10.1101/2023.05.01.538912"), ("pmid", "31178118"),
        ("pmid", "34017140"), ("pmcid", "PMC8457713"), ("arxiv", "2401.01234"),
        ("arxiv", "2310.06825"), ("arxiv", "2106.09685"),
    ]


def test_find_references_ignores_look_alikes_and_dedupes():
    assert find_references(["bump to 10.2.1, subnet 10.0.0.1/24, PMC is a database, "
                            "on 2026-08-13, chr1:10.5-20"]) == []
    refs = find_references([f"doi:{DOI}", f"see {DOI.upper()} again"])
    assert refs == [Reference("doi", DOI)]
    assert refs[0].url == f"https://doi.org/{DOI}" and refs[0].label == f"doi:{DOI}"


# ------------------------------------------------------------------ Zotero (Better BibTeX)


def _fake_bbt(library: list[dict]):
    """A real HTTP server answering BBT's item.search like a fuzzy Zotero search."""
    calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):  # noqa: N802 - http.server API
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            term = body["params"][0]
            calls.append(term)
            # Fuzzy on purpose: every item "matches", so the client must check identifiers.
            data = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": library}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/better-bibtex/json-rpc", calls


LIBRARY = [
    {"citekey": "harris2020array", "DOI": DOI.upper(), "title": "Array programming"},
    {"citekey": "wolf2018scanpy", "DOI": "10.1186/s13059-017-1382-0",
     "note": "PMID: 29409532"},
    {"citekey": "hu2021lora", "URL": "https://arxiv.org/abs/2106.09685"},
]


def test_citekeys_match_by_identifier_and_cache(tmp_path: Path):
    server, url, calls = _fake_bbt(LIBRARY)
    cache = tmp_path / "citekeys.json"
    try:
        cite = CitekeyResolver(BetterBibTeX(url), cache)
        assert cite(Reference("doi", DOI)) == "harris2020array"
        assert cite(Reference("pmid", "29409532")) == "wolf2018scanpy"
        assert cite(Reference("arxiv", "2106.09685")) == "hu2021lora"
        assert cite(Reference("pmid", "11111111")) is None  # fuzzy hits don't count
        assert cite(Reference("pmid", "11111111")) is None
        assert calls.count("11111111") == 1  # misses asked once per run
        cite.save()
    finally:
        server.shutdown()
    offline = CitekeyResolver(None, cache)  # cached hits work with Zotero closed
    assert offline(Reference("doi", DOI)) == "harris2020array"
    assert offline(Reference("pmid", "11111111")) is None


def test_closed_zotero_costs_one_attempt():
    cite = CitekeyResolver(BetterBibTeX("http://127.0.0.1:9/better-bibtex/json-rpc", 0.5))
    assert cite(Reference("doi", DOI)) is None
    assert cite.client is None  # later lookups don't retry
    assert cite(Reference("pmid", "1234567")) is None


# ------------------------------------------------------------------ vault


def test_day_meta_collects_references_from_prompts(tmp_path: Path):
    from devlog.knowledge import build_day_meta
    from devlog.models import SessionDigest
    from devlog.projects import ProjectResolver

    t0 = datetime(2026, 8, 13, 9).astimezone()
    digest = SessionDigest(
        session_id="s", project_path=str(tmp_path / "atlas"), source="claude_code",
        start_time=t0, end_time=t0 + timedelta(minutes=30), active_minutes=30.0,
        user_messages=["<system-reminder> doi:10.9999/injected", f"reproduce fig 2 of {DOI}",
                       "and PMID 29409532"])
    meta = build_day_meta(date(2026, 8, 13), [digest], "# x\n\ny",
                          ProjectResolver(home=tmp_path / "home"))
    assert meta["projects"][0]["references"] == [
        {"kind": "doi", "id": DOI}, {"kind": "pmid", "id": "29409532"}]


def test_vault_links_references_citekeys_and_literature_index(tmp_path: Path):
    from devlog.obsidian import refresh_vault

    day = _day("2026-08-13", "atlas", ["reproduce the scanpy clustering"])
    day["projects"][0]["references"] = [{"kind": "doi", "id": DOI},
                                        {"kind": "pmid", "id": "31178118"}]
    server, url, _ = _fake_bbt(LIBRARY)
    try:
        cfg, root = _vault(tmp_path, {"2026-08-13": day}, zotero_url=url)
        refresh_vault(cfg)
    finally:
        server.shutdown()

    note = (root / "2026-08-13.md").read_text(encoding="utf-8")
    assert "references: 2" in note and '  - "[[@harris2020array]]"' in note
    assert (f"- **References:** [[@harris2020array]] ([doi:{DOI}](<https://doi.org/{DOI}>)) · "
            "[PMID 31178118](<https://pubmed.ncbi.nlm.nih.gov/31178118/>)") in note
    lit = (root / "Literature.md").read_text(encoding="utf-8")
    assert "references: 2" in lit and "in_zotero: 1" in lit
    assert "[[@harris2020array\\|@harris2020array]]" in lit
    hub = (root / "Projects" / "atlas.md").read_text(encoding="utf-8")
    assert "## References" in hub and "[[@harris2020array]]" in hub
    topic = (root / "Topics" / "scanpy.md").read_text(encoding="utf-8")
    assert "## References seen with this topic" in topic
    home = (root / "DevLog Home.md").read_text(encoding="utf-8")
    assert "**2** paper(s) mentioned · **1** in Zotero" in home
    cache = json.loads((root / ".devlog" / "citekeys.json").read_text(encoding="utf-8"))
    assert cache == {f"doi:{DOI}": "harris2020array"}

    # No references left: the generated index goes away.
    day["projects"][0]["references"] = []
    cfg, _ = cfg, root
    from devlog.vault_graph import save_index

    save_index(root, {"2026-08-13": day})
    refresh_vault(cfg)
    assert not (root / "Literature.md").exists()


# ------------------------------------------------------------------ deck


def _deck_days() -> dict[str, dict]:
    a = _day("2026-09-21", "atlas", ["cluster the PBMC data with scanpy for MRN123456"],
             minutes=90, threads=["Check doublet rate"])
    a["projects"][0]["commits"] = [{"short": "abc", "subject": "Add leiden step", "url": None}]
    a["projects"][0]["pull_requests"] = [{"number": 7, "title": "UMAP", "url": "u",
                                          "state": "merged"}]
    a["projects"][0]["references"] = [{"kind": "doi", "id": DOI}]
    a["runs"] = [{"engine": "nextflow", "pipeline": "nf-core/scrnaseq", "slug": "nf-core-scrnaseq",
                  "status": "failed", "start": "2026-09-21T10:00", "minutes": 12.0,
                  "run_name": "r1", "version": "2.7.1", "error": "STAR ran out of memory",
                  "project_slug": "atlas"}]
    b = _day("2026-09-22", "secret-client", ["draft the grant aims"], minutes=30)
    b["projects"][0]["work_types"] = ["docs"]
    b["work_types"] = ["docs"]
    old = _day("2026-09-10", "atlas", ["older work"])
    return {"2026-09-21": a, "2026-09-22": b, "2026-09-10": old}


def test_resolve_scope():
    days = _deck_days()
    assert resolve_scope(days) == ("2026-W39", date(2026, 9, 21), date(2026, 9, 27))
    assert resolve_scope(days, week="latest")[0] == "2026-W39"
    assert resolve_scope(days, month="2026-08")[1:] == (date(2026, 8, 1), date(2026, 8, 31))
    assert resolve_scope(days, quarter="latest")[0] == "2026-Q3"
    assert resolve_scope(days, since="2026-09-01")[1:] == (date(2026, 9, 1), date(2026, 9, 22))


def _deck(detail: str, **kw) -> str:
    return build_deck(_deck_days(), label="2026-W39", start=date(2026, 9, 21),
                      end=date(2026, 9, 27), detail=detail, **kw)


def test_deck_detail_levels():
    summary = _deck("summary")
    assert summary.startswith("# Week 2026-W39 in review")
    assert "atlas" not in summary and "secret-client" not in summary
    assert "**2** active day(s) of 7, **2h 00m** of focused time" in summary
    assert "- **2** project(s)" in summary and "1 open thread(s) carried forward" in summary

    projects = _deck("projects")
    assert "| atlas | 1 | 1h 30m | 75% |" in projects
    assert "**atlas**: 1 commit(s), 1 PR(s)" in projects
    assert "| nf-core/scrnaseq | 1 | 0 | 12 min | 2.7.1 |" in projects
    assert "**atlas**: 1 open thread(s)" in projects
    for private in ("PBMC", "Add leiden step", "UMAP", "doublet", "STAR ran out", DOI):
        assert private not in projects
    assert "scanpy" in projects  # catalog topics are fine at this level

    verbatim = _deck("verbatim")
    for shown in ("cluster the PBMC data", "#7 UMAP (merged)", "Add leiden step",
                  "Check doublet rate", "STAR ran out of memory", f"https://doi.org/{DOI}"):
        assert shown in verbatim
    assert verbatim.count("\n---\n") >= 7  # slide breaks for Gamma / Marp


def test_deck_redacts_and_filters_by_project():
    configure_redaction([], ["mrn"])
    deck = _deck("verbatim")
    assert "MRN123456" not in deck and "[REDACTED]" in deck
    one = _deck("verbatim", project="Atlas")
    assert one.startswith("# atlas: Week 2026-W39") and "grant aims" not in one
    assert "Where the time went" not in one
    with pytest.raises(ValueError, match="nope"):
        _deck("projects", project="nope")


def test_deck_marks_unknown_project_minutes():
    days = _deck_days()
    days["2026-09-22"]["projects"][0]["minutes"] = None  # backfilled multi-project day
    deck = build_deck(days, label="2026-W39", start=date(2026, 9, 21), end=date(2026, 9, 27))
    assert "| secret-client | 1 | — |" in deck and "Share" not in deck


def test_cmd_deck_writes_outline(tmp_path: Path, capsys):
    from devlog.config import DevlogConfig, save_config

    cfg_path = tmp_path / "c.toml"
    save_config(DevlogConfig(obsidian_vault=str(tmp_path / "none")), cfg_path)
    assert cmd_deck(["--config", str(cfg_path)]) == 2
    assert "obsidian --backfill" in capsys.readouterr().out

    cfg, _ = _vault(tmp_path, _deck_days())
    save_config(cfg, cfg_path)
    out = tmp_path / "decks" / "w39.md"
    assert cmd_deck(["--week", "--detail", "projects", "--out", str(out),
                     "--config", str(cfg_path)]) == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Week 2026-W39 in review") and "PBMC" not in text
    assert "Paste in text" in capsys.readouterr().err
    assert cmd_deck(["--month", "2026-09", "--config", str(cfg_path)]) == 0
    assert capsys.readouterr().out.startswith("# September 2026 in review")
