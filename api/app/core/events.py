"""Event bus / webhook sink seam (Phase 4).

Typed events emitted from background-task paths, never the serving path:

    cache.miss, quota.exceeded, provider.failure, audit.failed

OSS default: ``WebhookEventSink`` POSTs to per-type webhook URLs from config
(``EVENTS_WEBHOOK_*``). No URL configured for an event type → that event is
dropped *before* any dispatch work happens, so an unconfigured deployment pays
nothing for this seam. Cloud overlay replaces the sink wholesale with a
Stripe/PagerDuty/Slack fan-out via ``set_event_sink()`` (see
docs/EXTENSION_SEAMS.md).

All dispatch is fail-open and off-path:

* emission never raises — a broken sink or webhook is logged and ignored;
* delivery is handed to Celery when the broker is online, otherwise run on a
  task/daemon thread, so a slow webhook can never add latency to a caller.

Payloads are operational metadata only. Prompt and response text are never
included, and credential-shaped keys (``api_key``, ``authorization``, …) are
stripped recursively before an event reaches any sink.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.request
from typing import Any, Protocol, runtime_checkable

from app.core.config import config

logger = logging.getLogger(__name__)

EVENT_CACHE_MISS = "cache.miss"
EVENT_QUOTA_EXCEEDED = "quota.exceeded"
EVENT_PROVIDER_FAILURE = "provider.failure"
EVENT_AUDIT_FAILED = "audit.failed"

_KNOWN_EVENTS = frozenset(
    {EVENT_CACHE_MISS, EVENT_QUOTA_EXCEEDED, EVENT_PROVIDER_FAILURE, EVENT_AUDIT_FAILED}
)

#: Config attribute holding the webhook URL for each event type.
_WEBHOOK_CONFIG = {
    EVENT_CACHE_MISS: "EVENTS_WEBHOOK_CACHE_MISS",
    EVENT_QUOTA_EXCEEDED: "EVENTS_WEBHOOK_QUOTA_EXCEEDED",
    EVENT_PROVIDER_FAILURE: "EVENTS_WEBHOOK_PROVIDER_FAILURE",
    EVENT_AUDIT_FAILED: "EVENTS_WEBHOOK_AUDIT_FAILED",
}

#: Payload keys that must never leave the gateway, matched case-insensitively
#: at any depth. Deliberately an exact-name set: ``token_count`` or
#: ``total_tokens`` are metrics, not credentials.
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "x-api-key",
        "authorization",
        "cookie",
        "password",
        "secret",
        "access_token",
        "refresh_token",
        "bearer",
    }
)


@runtime_checkable
class EventSink(Protocol):
    def emit(self, event: dict[str, Any]) -> None: ...


class NoopEventSink:
    def emit(self, event: dict[str, Any]) -> None:
        logger.debug("event: noop %s", event)


class WebhookEventSink:
    """OSS default: POST the event to the webhook URL configured for its type."""

    def __init__(self, urls: dict[str, str] | None = None) -> None:
        self._urls = urls if urls is not None else _urls_from_config()

    def emit(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        url = self._urls.get(event_type)
        if not url:
            logger.debug("event: no webhook for %s (dropped)", event_type)
            return
        payload = json.dumps(event, default=str).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"echo-gate/{config.VERSION}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:  # nosec - operator-configured URL
                if resp.status >= 400:
                    logger.warning(
                        "event webhook %s -> %s (ignored)", event_type, resp.status
                    )
        except Exception:  # fail-open by design
            logger.warning(
                "event webhook %s failed (ignored)", event_type, exc_info=True
            )


def _webhook_url(event_type: str) -> str:
    name = _WEBHOOK_CONFIG.get(event_type)
    if not name:
        return ""
    return (getattr(config, name, "") or "").strip()


def _urls_from_config() -> dict[str, str]:
    return {event_type: _webhook_url(event_type) for event_type in _KNOWN_EVENTS}


_sink: EventSink | None = None


def events_enabled() -> bool:
    """Whether this deployment may emit events at all (kill switch)."""
    return bool(getattr(config, "EVENTS_ENABLED", True))


def is_configured(event_type: str) -> bool:
    """Whether ``event_type`` has a destination, i.e. is worth building at all.

    Checked before dispatch so the OSS default (no webhook URLs) adds nothing
    to any request path.
    """
    if not events_enabled():
        return False
    if _sink is not None:
        # An injected sink (cloud overlay) decides for itself; it may fan out
        # to destinations that are not in config.
        return True
    return bool(_webhook_url(event_type))


def default_sink() -> EventSink:
    return _sink or WebhookEventSink()


def set_event_sink(sink: EventSink | None) -> None:
    """Cloud overlay injection point (mirrors metering.default_sink swap)."""
    global _sink
    _sink = sink


def _sanitize(value: Any) -> Any:
    """Recursively drop credential-shaped keys from an event payload."""
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if str(key).strip().lower() not in _SENSITIVE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


def _build_event(
    event_type: str, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    if event_type not in _KNOWN_EVENTS:
        logger.debug("event: unknown type %s (dropped)", event_type)
        return None
    return {
        "type": event_type,
        "ts": time.time(),
        "payload": _sanitize(payload or {}),
    }


def deliver(event: dict[str, Any]) -> None:
    """Deliver one event to the sink. Never raises."""
    try:
        default_sink().emit(event)
    except Exception:  # pragma: no cover - fail-open by design
        logger.warning("event sink failed (ignored)", exc_info=True)


def _celery_delivery_task():
    """Resolve the Celery delivery task, or ``None`` when Celery is unavailable.

    The task lives in ``app.core.tasks`` (where Celery tasks belong) and is
    imported lazily: importing ``app.worker.celery_app`` at module scope would
    create an import cycle with this module, which previously made task
    registration depend on import order.
    """
    try:
        from app.core.tasks import deliver_event

        return deliver_event
    except Exception:  # pragma: no cover - celery not installed/configured
        logger.debug("event celery task unavailable; using in-process delivery")
        return None


def _dispatch(event: dict[str, Any]) -> None:
    """Hand delivery to Celery, else to a task/thread. Never blocks the caller."""
    try:
        from app.core.helpers import is_celery_broker_online

        if is_celery_broker_online():
            task = _celery_delivery_task()
            if task is not None:
                try:
                    task.delay(event)
                    return
                except Exception:
                    logger.warning(
                        "event celery dispatch failed (ignored)", exc_info=True
                    )
    except Exception:  # pragma: no cover - broker probe unavailable
        logger.debug("event broker probe failed; using in-process delivery")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Sync caller with no event loop: use a daemon thread so a slow or
        # hanging webhook cannot delay the caller.
        threading.Thread(
            target=deliver, args=(event,), name="event-delivery", daemon=True
        ).start()
        return

    loop.create_task(asyncio.to_thread(deliver, event))


def emit_event(
    event_type: str,
    payload: dict[str, Any] | None = None,
    sink: EventSink | None = None,
) -> dict[str, Any] | None:
    """Build + dispatch one typed event. Fail-open; never raises.

    Returns the dispatched event, or ``None`` when the type is unknown or the
    deployment has not configured a destination for it.
    """
    event = _build_event(event_type, payload)
    if event is None:
        return None

    if sink is not None:
        try:
            sink.emit(event)
        except Exception:  # pragma: no cover - fail-open by design
            logger.warning("event sink failed (ignored)", exc_info=True)
        return event

    if not is_configured(event_type):
        logger.debug("event: %s has no destination (dropped)", event_type)
        return None

    try:
        _dispatch(event)
    except Exception:  # pragma: no cover - fail-open by design
        logger.warning("event dispatch failed (ignored)", exc_info=True)
    return event


# --- Typed emitters -------------------------------------------------------
#
# Thin, documented wrappers so call sites read as intent. Every one is safe to
# call from a background task: they never raise and never perform webhook I/O
# inline.


def emit_cache_miss(
    *,
    exact_hash: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    intent: str | None = None,
    source: str = "provider",
    cache_mode: str | None = None,
    latency_ms: int | None = None,
) -> dict[str, Any] | None:
    """A request missed the cache and was served by a provider."""
    return emit_event(
        EVENT_CACHE_MISS,
        {
            "exact_hash": exact_hash,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "provider": provider,
            "model": model,
            "intent": intent,
            "source": source,
            "cache_mode": cache_mode,
            "latency_ms": latency_ms,
        },
    )


def emit_quota_exceeded(
    *,
    reason: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    limit: int | None = None,
    used: int | None = None,
    retry_after_seconds: int | None = None,
) -> dict[str, Any] | None:
    """A caller was refused by a quota/rate/budget limit.

    ``reason`` is one of ``rate_limit``, ``token_limit``, ``budget_exhausted``.
    ``user_id`` is the identity resolved from the API key, never the
    caller-supplied ``user`` body field.
    """
    return emit_event(
        EVENT_QUOTA_EXCEEDED,
        {
            "reason": reason,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "provider": provider,
            "model": model,
            "limit": limit,
            "used": used,
            "retry_after_seconds": retry_after_seconds,
        },
    )


def emit_provider_failure(
    *,
    provider: str,
    model: str | None = None,
    error_code: str | None = None,
    status_code: int | None = None,
    detail: str | None = None,
    tenant_id: str | None = None,
    final: bool = False,
) -> dict[str, Any] | None:
    """One upstream provider attempt failed.

    ``final`` marks the last candidate, i.e. the failure that the caller sees.
    ``detail`` is the provider's short error text; it never contains the prompt.
    """
    return emit_event(
        EVENT_PROVIDER_FAILURE,
        {
            "provider": provider,
            "model": model,
            "error_code": error_code,
            "status_code": status_code,
            "detail": detail,
            "tenant_id": tenant_id,
            "final": final,
        },
    )


def emit_audit_failed(
    *,
    exact_hash: str | None,
    tenant_id: str | None = None,
    model: str | None = None,
    match_type: str | None = None,
    rerank_score: float | None = None,
    vector_similarity: float | None = None,
    evicted: bool = False,
    reason: str = "non_equivalent",
) -> dict[str, Any] | None:
    """A sampled cache audit did not validate its entry.

    ``reason`` is ``non_equivalent`` (the cached answer disagreed with a fresh
    one), ``fresh_completion_failed`` (no ground truth could be fetched), or
    ``audit_error`` (the audit itself raised).
    """
    return emit_event(
        EVENT_AUDIT_FAILED,
        {
            "exact_hash": exact_hash,
            "tenant_id": tenant_id,
            "model": model,
            "match_type": match_type,
            "rerank_score": rerank_score,
            "vector_similarity": vector_similarity,
            "evicted": evicted,
            "reason": reason,
        },
    )

