"""Metering export seam.

``LlmRouterService._log_usage`` writes usage rows to Postgres already. This
module adds a typed-event fan-out behind a ``MeteringSink`` protocol so a
cloud overlay can install a Stripe recorder (or another sink) without
changing the router: OSS defaults to a ``noop`` sink.

Events are plain dicts matching the ``llm_usage`` columns the router
already computes (``provider_cost``, ``token_count``, ``tenant_id``,
``model``, ``service_name``) — the cloud syncs them into Stripe usage
records and decrements the Redis ``budget:`` counter.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class MeteringSink(Protocol):
    """A usage-event sink. Implementations must be fail-open."""

    def emit(self, event: dict[str, Any]) -> None: ...


class NoopMeteringSink:
    """OSS default: records nothing (usage still lands in Postgres)."""

    def emit(self, event: dict[str, Any]) -> None:
        logger.debug("meter: noop %s", event)


def default_sink() -> MeteringSink:
    """The sink the OSS gateway uses (a cloud overlay overrides this)."""
    return NoopMeteringSink()


def emit_usage(event: dict[str, Any], sink: MeteringSink | None = None) -> None:
    """Fan out one usage event. Fail-open: sink errors are logged, never raised."""
    try:
        (sink or default_sink()).emit(event)
    except Exception:  # pragma: no cover - fail-open by design
        logger.warning("Metering sink failed (ignored)", exc_info=True)
