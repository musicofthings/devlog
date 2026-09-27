"""Estimate what a day's AI sessions cost from per-model token counts.

Default prices are Anthropic first-party API rates, USD per million tokens
(input, output, cache read). Where Anthropic states no cache-read rate, the
documented ~0.1x-of-input approximation is used. Prices change and other
providers aren't included: add or override any model in config.toml

    [model_prices]
    "gpt-5-codex" = [1.25, 10.0, 0.125]

Tokens from models without a price are reported as unpriced, never guessed.
Subscription plans (Claude Max, ChatGPT Pro) aren't billed per token, so treat
the figure as "API-equivalent cost".
"""

from __future__ import annotations

import re

DEFAULT_PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-fable-5": (10.0, 50.0, 1.0),
    "claude-mythos-5-1": (10.0, 50.0, 0.25),  # same pricing as Fable 5.1
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.5),
    "claude-opus-4-8": (5.0, 25.0, 0.5),
    "claude-opus-4-7": (5.0, 25.0, 0.5),
    "claude-opus-4-6": (5.0, 25.0, 0.5),
    "claude-sonnet-5": (2.0, 10.0, 0.2),
    "claude-sonnet-4-6": (3.0, 15.0, 0.3),
    "claude-haiku-4-5": (1.0, 5.0, 0.1),
}
_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
_CONTEXT_SUFFIX_RE = re.compile(r"\[[^\]]*\]$")


def normalize_model(model: str) -> str:
    """'claude-opus-4-8-20260101[1m]' -> 'claude-opus-4-8'."""
    model = _CONTEXT_SUFFIX_RE.sub("", model.strip().lower())
    return _DATE_SUFFIX_RE.sub("", model)


def price_table(overrides: dict[str, list[float]] | None = None) -> dict[str, tuple]:
    table = dict(DEFAULT_PRICES)
    for model, rates in (overrides or {}).items():
        table[normalize_model(model)] = (float(rates[0]), float(rates[1]), float(rates[2]))
    return table


def estimate(tokens_by_model: dict[str, dict[str, int]] | None,
             prices: dict[str, tuple]) -> tuple[float, int]:
    """(USD for priced models, token count that had no price)."""
    usd = 0.0
    unpriced = 0
    for model, t in (tokens_by_model or {}).items():
        rates = prices.get(normalize_model(model))
        n_in, n_out, n_cache = (int(t.get(k) or 0) for k in ("in", "out", "cache"))
        if rates is None:
            unpriced += n_in + n_out + n_cache
            continue
        usd += (n_in * rates[0] + n_out * rates[1] + n_cache * rates[2]) / 1_000_000
    return usd, unpriced


def merge_tokens(*maps: dict[str, dict[str, int]] | None) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for m in maps:
        for model, t in (m or {}).items():
            bucket = out.setdefault(model, {"in": 0, "out": 0, "cache": 0})
            for k in bucket:
                bucket[k] += int(t.get(k) or 0)
    return out


def format_cost(usd: float, unpriced: int) -> str:
    if usd <= 0 and unpriced:
        return ""
    text = f"≈ ${usd:,.2f}" if usd >= 0.01 else "≈ <$0.01"
    return text + (" + unpriced models" if unpriced else "")
