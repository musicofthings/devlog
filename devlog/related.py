"""Local "related days" / search: TF-IDF cosine similarity, pure Python.

No network and no model download: good enough to connect "scanpy QC on the
atlas" in one project with "fix the h5ad loader" weeks later in another, and
to back keyword search for the MCP memory server. Shared terms are reported
so every link explains itself.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z][a-z0-9_+.-]{2,}")
STOPWORDS = frozenset(
    """
    the and for with that this from into onto have has had are was were will would should
    could can not but all any our your you its they them then than there here what when
    where which who how why also just now new use used using make made more most some such
    very over only out off per via let lets get got see run ran add added adding update
    updated fix fixed file files code repo today logged active min worked tools read edit
    write bash grep glob shell exec session sessions review please need want like done
    next step steps follow ups todo continue try again one two back yes okay sure check
    """.split()
)
MIN_SCORE = 0.12


def tokenize(text: str) -> list[str]:
    tokens = []
    for tok in _TOKEN_RE.findall(text.lower()):
        tok = tok.strip(".-+")
        if len(tok) >= 3 and tok not in STOPWORDS and not tok.isdigit():
            tokens.append(tok)
    return tokens


class Corpus:
    """TF-IDF vectors for a small set of documents keyed by id."""

    def __init__(self, docs: dict[str, str]) -> None:
        counts = {key: Counter(tokenize(text)) for key, text in docs.items()}
        df: Counter = Counter()
        for c in counts.values():
            df.update(c.keys())
        n = max(len(counts), 1)
        self.idf = {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}
        self.vectors: dict[str, dict[str, float]] = {}
        for key, c in counts.items():
            vec = {t: (1 + math.log(f)) * self.idf[t] for t, f in c.items()}
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            self.vectors[key] = {t: v / norm for t, v in vec.items()}

    def _query_vector(self, text: str) -> dict[str, float]:
        c = Counter(t for t in tokenize(text) if t in self.idf)
        vec = {t: (1 + math.log(f)) * self.idf[t] for t, f in c.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    @staticmethod
    def _score(a: dict[str, float], b: dict[str, float]) -> tuple[float, list[str]]:
        if len(a) > len(b):
            a, b = b, a
        shared = {t: a[t] * b[t] for t in a if t in b}
        top = [t for t, _ in sorted(shared.items(), key=lambda kv: -kv[1])[:3]]
        return sum(shared.values()), top

    def shared_terms(self, a: str, b: str) -> list[str]:
        """Top terms two documents share (explains a link from any backend)."""
        return self._score(self.vectors.get(a, {}), self.vectors.get(b, {}))[1]

    def similar(self, key: str, k: int = 3, min_score: float = MIN_SCORE,
                exclude: set[str] | None = None) -> list[tuple[str, float, list[str]]]:
        """Nearest documents to `key`: (other_key, score, shared_terms)."""
        base = self.vectors.get(key)
        if not base:
            return []
        exclude = (exclude or set()) | {key}
        scored = []
        for other, vec in self.vectors.items():
            if other in exclude:
                continue
            score, terms = self._score(base, vec)
            if score >= min_score:
                scored.append((other, score, terms))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:k]

    def search(self, query: str, k: int = 10) -> list[tuple[str, float, list[str]]]:
        q = self._query_vector(query)
        if not q:
            return []
        scored = []
        for key, vec in self.vectors.items():
            score, terms = self._score(q, vec)
            if score > 0:
                scored.append((key, score, terms))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:k]


def day_document(meta: dict, topic_names: list[str] | None = None) -> str:
    """The text of a day that similarity should look at (not template filler)."""
    parts: list[str] = []
    projects = meta.get("projects") or []
    for p in projects:
        parts.append(p.get("name") or p.get("slug") or "")
        parts += p.get("tasks") or []
        parts += p.get("threads") or []
        parts += [c.get("subject") or "" for c in p.get("commits") or []]
        parts += [f.rsplit(".", 1)[0] for f in p.get("files") or []]
    parts += meta.get("work_types") or []
    # Topics are the strongest signal; count them twice.
    parts += (topic_names or []) * 2
    if not projects:
        parts.append(meta.get("summary") or "")
    return "\n".join(parts)


def cosine_neighbors(vectors: dict[str, list[float]], key: str, k: int = 3,
                     min_score: float = 0.55) -> list[tuple[str, float]]:
    """Nearest keys by cosine similarity of (embedding) vectors."""
    base = vectors.get(key)
    if not base:
        return []
    base_norm = math.sqrt(sum(x * x for x in base)) or 1.0
    scored = []
    for other, vec in vectors.items():
        if other == key or not vec or len(vec) != len(base):
            continue
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        score = sum(a * b for a, b in zip(base, vec, strict=True)) / (base_norm * norm)
        if score >= min_score:
            scored.append((other, score))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:k]
