"""Find paper identifiers in prompts and link them in the vault (never in posts).

Recognized: DOIs (including doi.org URLs and bioRxiv/medRxiv, which are DOIs),
PubMed IDs (`PMID: 12345678`, pubmed.ncbi.nlm.nih.gov/12345678), PMC IDs
(PMC1234567), and arXiv IDs (arXiv:2401.01234, arxiv.org/abs/2401.01234v2).

When Zotero is running with Better BibTeX, each identifier is looked up over
BBT's local JSON-RPC endpoint and, if the paper is in your library, linked as
`[[@citekey]]`, the note name the Zotero Integration plugin uses for literature
notes. Found citekeys are cached in the vault (`.devlog/citekeys.json`); a
paper added to Zotero later is picked up on the next refresh.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BBT_URL = "http://localhost:23119/better-bibtex/json-rpc"


def zotero_installed(platform: str | None = None) -> bool:
    """Whether Zotero looks installed here (its data folder, or the app itself)."""
    home = Path.home()
    if (home / "Zotero").is_dir() or shutil.which("zotero"):  # ~/Zotero: default data dir
        return True
    platform = platform or sys.platform
    if platform == "darwin":
        apps = [Path("/Applications/Zotero.app"), home / "Applications" / "Zotero.app"]
    elif platform == "win32":
        roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        apps = [Path(r) / "Zotero" / "zotero.exe" for r in roots if r]
        apps += [Path(r) / "Programs" / "Zotero" / "zotero.exe" for r in roots[2:] if r]
    else:
        apps = [Path("/opt/zotero"), Path("/usr/lib/zotero"), home / ".local" / "share" / "zotero"]
    return any(app.exists() for app in apps)
MAX_REFS_PER_PROJECT = 12

# DOI: "10." + registrant + "/" + suffix; the suffix stops at whitespace, quotes,
# and brackets, and loses trailing punctuation (". , ; :" and an unmatched ")").
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>\[\]{}|\\^`]+)", re.IGNORECASE)
_PMID_RE = re.compile(
    r"(?:\bPMID\s*[:#]?\s*|pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/)(\d{5,9})\b",
    re.IGNORECASE,
)
_PMC_RE = re.compile(r"\b(PMC\d{5,9})\b", re.IGNORECASE)
_ARXIV_RE = re.compile(
    r"(?:\barxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE
)


@dataclass(frozen=True)
class Reference:
    kind: str  # "doi" | "pmid" | "pmcid" | "arxiv"
    id: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"

    @property
    def url(self) -> str:
        return {
            "doi": f"https://doi.org/{self.id}",
            "pmid": f"https://pubmed.ncbi.nlm.nih.gov/{self.id}/",
            "pmcid": f"https://pmc.ncbi.nlm.nih.gov/articles/{self.id}/",
            "arxiv": f"https://arxiv.org/abs/{self.id}",
        }[self.kind]

    @property
    def label(self) -> str:
        return {"doi": f"doi:{self.id}", "pmid": f"PMID {self.id}",
                "pmcid": self.id, "arxiv": f"arXiv:{self.id}"}[self.kind]


def _clean_doi(raw: str) -> str:
    doi = raw.rstrip(".,;:")
    while doi.endswith(")") and doi.count("(") < doi.count(")"):
        doi = doi[:-1].rstrip(".,;:")
    # A DOI URL with a trailing fragment/query isn't part of the identifier.
    doi = re.split(r"[?#]", doi, maxsplit=1)[0]
    # arXiv's own DOIs duplicate the arXiv ID; keep the arXiv form.
    return doi.lower() if doi.lower().startswith("10.48550/") else doi


def find_references(texts: list[str]) -> list[Reference]:
    """Identifiers in `texts`, in order of first appearance, deduplicated."""
    found: list[tuple[int, int, Reference]] = []
    for i, text in enumerate(texts):
        for m in _DOI_RE.finditer(text):
            doi = _clean_doi(m.group(1))
            arxiv = re.match(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})", doi)
            ref = Reference("arxiv", arxiv.group(1)) if arxiv else Reference("doi", doi)
            found.append((i, m.start(), ref))
        found += [(i, m.start(), Reference("pmid", m.group(1))) for m in _PMID_RE.finditer(text)]
        found += [(i, m.start(), Reference("pmcid", m.group(1).upper()))
                  for m in _PMC_RE.finditer(text)]
        found += [(i, m.start(), Reference("arxiv", m.group(1))) for m in _ARXIV_RE.finditer(text)]
    seen: dict[str, Reference] = {}
    for _, _, ref in sorted(found, key=lambda t: (t[0], t[1])):
        # DOIs are case-insensitive; dedupe on the folded form.
        seen.setdefault(ref.key.lower(), ref)
    return list(seen.values())


class ZoteroError(Exception):
    pass


class BetterBibTeX:
    """Minimal client for Better BibTeX's JSON-RPC endpoint (Zotero must be running)."""

    def __init__(self, url: str = DEFAULT_BBT_URL, timeout: float = 3.0) -> None:
        self.url = url
        self.timeout = timeout

    def search(self, terms: str) -> list[dict]:
        body = json.dumps({"jsonrpc": "2.0", "method": "item.search",
                           "params": [terms], "id": 1}).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json",
                                          "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ZoteroError(str(exc)) from exc
        if not isinstance(data, dict) or "error" in data:
            raise ZoteroError(str(data.get("error") if isinstance(data, dict) else data))
        result = data.get("result")
        return result if isinstance(result, list) else []


def _item_matches(item: dict, ref: Reference) -> bool:
    """Only accept a search hit that carries the same identifier (search is fuzzy)."""
    want = ref.id.lower()
    if ref.kind == "doi":
        return str(item.get("DOI") or "").lower() == want
    extra = str(item.get("note") or item.get("extra") or "").lower()
    if ref.kind == "pmid":
        return str(item.get("PMID") or "") == ref.id or f"pmid: {want}" in extra
    if ref.kind == "pmcid":
        return str(item.get("PMCID") or "").lower() == want or f"pmcid: {want}" in extra
    url = str(item.get("URL") or "").lower()
    number = str(item.get("number") or "").lower()
    return want in url or want in number or f"arxiv:{want}" in extra \
        or str(item.get("DOI") or "").lower() == f"10.48550/arxiv.{want}"


def _citekey(item: dict) -> str | None:
    for field in ("citekey", "citationKey", "citation-key", "id"):
        value = item.get(field)
        if isinstance(value, str) and value and " " not in value:
            return value
    return None


class CitekeyResolver:
    """Reference -> Better BibTeX citekey, with a vault cache of the ones found.

    One failed connection turns lookups off for the rest of the run, so a
    closed Zotero costs one timeout, not one per reference.
    """

    def __init__(self, client: BetterBibTeX | None, cache_path: Path | None = None) -> None:
        self.client = client
        self.cache_path = cache_path
        self.cache: dict[str, str] = {}
        self._missing: set[str] = set()
        self._dirty = False
        if cache_path is not None and cache_path.is_file():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                self.cache = {k: v for k, v in data.items()
                              if isinstance(k, str) and isinstance(v, str)}
            except (OSError, ValueError, AttributeError):
                self.cache = {}

    def __call__(self, ref: Reference) -> str | None:
        key = ref.key.lower()
        if key in self.cache:
            return self.cache[key]
        if self.client is None or key in self._missing:
            return None
        try:
            items = self.client.search(ref.id)
        except ZoteroError:
            self.client = None
            return None
        citekey = next((_citekey(i) for i in items if isinstance(i, dict)
                        and _item_matches(i, ref) and _citekey(i)), None)
        if citekey is None:
            self._missing.add(key)
            return None
        self.cache[key] = citekey
        self._dirty = True
        return citekey

    def save(self) -> None:
        if self.cache_path is None or not self._dirty:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, indent=1, sort_keys=True),
                                   encoding="utf-8")
        self._dirty = False


def reference_dicts(texts: list[str]) -> list[dict]:
    """Serializable references for the vault index."""
    return [{"kind": r.kind, "id": r.id} for r in find_references(texts)[:MAX_REFS_PER_PROJECT]]


def as_reference(data: dict) -> Reference:
    return Reference(str(data["kind"]), str(data["id"]))
