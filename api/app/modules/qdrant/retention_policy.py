"""Value-aware cache retention (vCache-style).

Cache space is not free and not every entry saves the same thing. A slow,
expensive reasoning-model answer is worth keeping far longer than a two-token
greeting, yet a plain ``expires_at < now`` sweep evicts both identically.

This module holds the pure scoring functions used by the cache write path and
the eviction task:

* ``estimate_provider_cost`` — what re-generating this entry would have cost.
* ``calculate_ttl_seconds`` — the intent TTL, stretched by entry value.
* ``compute_retention_value`` — a comparable score for eviction ordering.

They stay free of I/O so the curves are unit-testable without Qdrant, Redis, or
a provider in the loop.
"""

import math

from app.core.pricing import get_model_prices


def _non_negative(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return max(float(value), 0.0)


def estimate_provider_cost(
    provider: str,
    model: str,
    prompt_tokens: int | None = 0,
    completion_tokens: int | None = 0,
) -> float:
    """Provider spend (USD) that serving this request from cache avoided."""
    prompt_price, completion_price = get_model_prices(provider, model)
    return round(
        _non_negative(prompt_tokens) * prompt_price
        + _non_negative(completion_tokens) * completion_price,
        8,
    )


def retention_multiplier(
    provider_cost: float | None, latency_ms: float | None
) -> float:
    """How much longer than the base TTL an entry deserves.

    ``1 + log10(1 + cost * 100)`` keeps free/local models at ~1x while a
    $0.02 answer earns ~1.5x; ``1 + latency_ms / 1000`` rewards responses that
    took seconds to generate, which is the latency a cache hit saves.
    """
    cost_component = 1.0 + math.log10(1.0 + _non_negative(provider_cost) * 100.0)
    latency_component = 1.0 + _non_negative(latency_ms) / 1000.0
    return cost_component * latency_component


def calculate_ttl_seconds(
    base_ttl_seconds: int,
    provider_cost: float | None,
    latency_ms: float | None,
    max_ttl_seconds: int,
) -> int:
    """Stretch ``base_ttl_seconds`` by entry value, capped at ``max_ttl_seconds``.

    The base TTL still comes from the intent policy, so this only ever extends
    an entry's life — nothing that used to be cached for a shorter time becomes
    long-lived because of a cheap model or a fast response.
    """
    scaled_seconds = _non_negative(base_ttl_seconds) * retention_multiplier(
        provider_cost, latency_ms
    )
    return int(min(scaled_seconds, _non_negative(max_ttl_seconds)))


def compute_retention_value(
    provider_cost: float | None,
    latency_ms: float | None,
    token_count: int | None,
    hit_count: int | None = 0,
) -> float:
    """Dimensionless value score used to order eviction.

    Additive components keep cheap entries near 1.0 (a 0.3 s greeting is ~1.4,
    an 8 s Claude code answer ~10) while ``hit_count`` scales the whole score,
    because an entry that is actually being served is saving its cost over and
    over. Higher is more valuable; the eviction sweep deletes lowest first.
    """
    cost_component = math.log10(1.0 + _non_negative(provider_cost) * 100.0)
    latency_component = _non_negative(latency_ms) / 1000.0
    token_component = math.log10(1.0 + _non_negative(token_count) / 1000.0)
    hit_component = 1.0 + math.log10(1.0 + _non_negative(hit_count))
    return round(
        (1.0 + cost_component + latency_component + token_component) * hit_component,
        6,
    )
