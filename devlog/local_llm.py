"""Optional local models (Ollama) for the vault: embeddings and weekly retros.

Everything here talks to an Ollama server on this machine (default
http://localhost:11434), never to an external API, so transcript-derived text
stays local. Both features are off unless enabled in config.toml:

    related_backend = "ollama"            # "tfidf" (default) | "ollama"
    ollama_embed_model = "nomic-embed-text"
    period_retros = true                  # weekly/monthly retro notes
    ollama_model = "llama3.2"
    ollama_url = "http://localhost:11434"

Results are cached under DevLog/.devlog/ keyed by a hash of their input, so a
refresh only calls the model for days/periods whose content changed. Any
failure (server down, model missing) falls back to the deterministic output.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

TIMEOUT_S = 120
RETRO_PROMPT = (
    "You write a short retrospective for a developer's private work journal.\n"
    "Use ONLY the facts below. 3 or 4 plain sentences, first person, no lists, no hype, "
    "no invented details. Say what the period focused on, what changed versus the previous "
    "period, and what looks unfinished.\n\nFacts:\n"
)


class LocalLLMError(RuntimeError):
    pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


class OllamaClient:
    def __init__(self, url: str = "http://localhost:11434", opener: Callable | None = None):
        self.url = url.rstrip("/")
        self._open = opener or urllib.request.urlopen

    def _post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            self.url + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._open(request, timeout=TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise LocalLLMError(f"Ollama request to {self.url}{path} failed: {exc}") from exc

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        data = self._post("/api/embed", {"model": model, "input": texts})
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise LocalLLMError("Ollama returned no embeddings")
        return vectors

    def generate(self, prompt: str, model: str) -> str:
        data = self._post("/api/generate", {"model": model, "prompt": prompt, "stream": False})
        text = data.get("response")
        if not isinstance(text, str) or not text.strip():
            raise LocalLLMError("Ollama returned an empty response")
        return text.strip()


class _JsonCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.dirty = False

    def save(self) -> None:
        if self.dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
            self.dirty = False


class CachedEmbedder:
    """docs {key: text} -> {key: vector}; only uncached texts hit the model."""

    def __init__(self, client: OllamaClient, model: str, cache_dir: Path) -> None:
        self.client = client
        self.model = model
        self.cache = _JsonCache(cache_dir / "embeddings.json")

    def __call__(self, docs: dict[str, str]) -> dict[str, list[float]]:
        keys = {k: f"{self.model}:{_sha(t)}" for k, t in docs.items()}
        missing = [k for k in docs if keys[k] not in self.cache.data]
        if missing:
            vectors = self.client.embed([docs[k] for k in missing], self.model)
            for k, vec in zip(missing, vectors, strict=True):
                self.cache.data[keys[k]] = vec
            self.cache.dirty = True
        # Drop vectors for documents that no longer exist.
        live = set(keys.values())
        stale = [k for k in self.cache.data if k.startswith(f"{self.model}:") and k not in live]
        for k in stale:
            del self.cache.data[k]
            self.cache.dirty = True
        self.cache.save()
        return {k: self.cache.data[keys[k]] for k in docs}


class CachedRetro:
    """(label, facts) -> retro text, regenerated only when the facts change."""

    def __init__(self, client: OllamaClient, model: str, cache_dir: Path) -> None:
        self.client = client
        self.model = model
        self.cache = _JsonCache(cache_dir / "retros.json")

    def __call__(self, label: str, facts: str) -> str | None:
        key = _sha(self.model + "\n" + facts)
        cached = self.cache.data.get(label)
        if isinstance(cached, dict) and cached.get("key") == key:
            return cached.get("text")
        try:
            text = self.client.generate(RETRO_PROMPT + facts, self.model)
        except LocalLLMError:
            return None  # a stale retro for changed facts would mislead
        self.cache.data[label] = {"key": key, "text": text}
        self.cache.dirty = True
        self.cache.save()
        return text
