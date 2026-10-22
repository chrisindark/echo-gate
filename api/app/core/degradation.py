"""Explicit degradation contract for the gateway serving path.

Every completion response carries an ``X-Gateway-Degradation`` header describing
how far the request path had to bend to answer:

* ``none`` — the answer came from the cache or the primary provider.
* ``cache_bypassed`` — a cache layer (Redis or Qdrant) failed open, so the
  request was forwarded to the provider instead of being answered from cache.
* ``provider_fallback`` — the primary provider was unavailable, rate-limited, or
  short-circuited by its circuit breaker, so a fallback provider or the next
  alias target answered.

The cache flag is tracked in a :class:`~contextvars.ContextVar` rather than on a
service instance because the fail-open decisions happen deep in
``RedisService`` and ``LlmCacheCollectionService``, which must not learn about
request objects. Each request gets its own scope, so concurrent requests cannot
observe each other's degradation.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

logger = logging.getLogger(__name__)

DEGRADATION_NONE = "none"
DEGRADATION_CACHE_BYPASSED = "cache_bypassed"
DEGRADATION_PROVIDER_FALLBACK = "provider_fallback"

_cache_degraded: ContextVar[bool] = ContextVar("gateway_cache_degraded", default=False)


@contextmanager
def cache_degradation_scope() -> Iterator[None]:
    """Reset the per-request cache-degradation flag around a serving path."""
    token = _cache_degraded.set(False)
    try:
        yield
    finally:
        _cache_degraded.reset(token)


def mark_cache_degraded(reason: str) -> None:
    """Record that a cache layer failed open for the current request."""
    if not _cache_degraded.get():
        logger.warning(
            "Cache layer degraded (%s); this request will be served by the provider",
            reason,
        )
    _cache_degraded.set(True)


def is_cache_degraded() -> bool:
    return _cache_degraded.get()


def resolve_degradation_state(
    provider_state: str | None, *, served_from_cache: bool = False
) -> str:
    """Combine the provider-path state with the cache-degradation flag.

    ``provider_fallback`` dominates: it already tells the client that the answer
    is not from the provider it asked for. ``cache_bypassed`` is only reported
    when the answer actually came from a provider, because the documented
    meaning is "the cache was unavailable, so we called upstream".
    """
    if provider_state == DEGRADATION_PROVIDER_FALLBACK:
        return DEGRADATION_PROVIDER_FALLBACK
    if not served_from_cache and is_cache_degraded():
        return DEGRADATION_CACHE_BYPASSED
    return DEGRADATION_NONE
