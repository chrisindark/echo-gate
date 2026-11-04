"""OpenTelemetry tracing support and GenAI semantic conventions for Echo Gate.

This module provides standard tracer access, context managers, and attribute
keys compliant with OpenTelemetry's GenAI Semantic Conventions.

When no OpenTelemetry SDK / exporter is configured, the tracer operates as a
lightweight NoOp/NonRecordingSpan, adding zero serving overhead. When an
OTLP exporter is configured (such as an OpenTelemetry collector, Langfuse OTLP
endpoint, Datadog, or Jaeger), spans are exported automatically.

Exporter setup is opt-in via :func:`init_telemetry`, called from the app
lifespan. The ``X-Correlation-ID`` header is used as the trace ID so gateway
logs and exported traces join on one id.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode, Tracer

from app.core.config import config

logger = logging.getLogger(__name__)

# =====================================================================
# GenAI Semantic Conventions (OpenTelemetry Standard)
# =====================================================================
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_TOP_P = "gen_ai.request.top_p"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_USAGE_PROMPT_TOKENS = "gen_ai.usage.prompt_tokens"
GEN_AI_USAGE_COMPLETION_TOKENS = "gen_ai.usage.completion_tokens"
GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"

# =====================================================================
# Echo Gate Gateway Custom Attributes
# =====================================================================
ECHOGATE_CACHE_HIT = "echogate.cache.hit"
ECHOGATE_CACHE_LAYER = "echogate.cache.layer"
ECHOGATE_ROUTING_DECISION = "echogate.routing_decision"
ECHOGATE_TENANT_ID = "echogate.tenant_id"
ECHOGATE_USER_ID = "echogate.user_id"
ECHOGATE_SESSION_ID = "echogate.session_id"
ECHOGATE_CONVERSATION_ID = "echogate.conversation_id"
ECHOGATE_DEGRADATION_STATE = "echogate.degradation_state"
ECHOGATE_EXACT_HASH = "echogate.exact_hash"
ECHOGATE_ERROR_CODE = "echogate.error_code"

# =====================================================================
# Standard Span Names
# =====================================================================
SPAN_GENERATE_COMPLETION = "echogate.generate_completion"
SPAN_GENERATE_STREAM = "echogate.generate_stream"
SPAN_CACHE_REDIS = "echogate.cache.redis_exact"
SPAN_CACHE_QDRANT_EXACT = "echogate.cache.qdrant_exact"
SPAN_CACHE_SEMANTIC = "echogate.cache.semantic_search"
SPAN_INTENT_CLASSIFIER = "echogate.intent_classifier"
SPAN_RERANKER = "echogate.reranker"
SPAN_PROVIDER_CALL = "echogate.provider.call"


def get_tracer(name: str | None = None) -> Tracer:
    """Return an OpenTelemetry tracer for Echo Gate."""
    service_name = name or getattr(config, "OTEL_SERVICE_NAME", "echo-gate")
    return trace.get_tracer(service_name)


def init_telemetry() -> None:
    """Install the OTLP exporter when an endpoint is configured.

    No-op when ``OTEL_ENABLED`` is false or ``OTEL_EXPORTER_OTLP_ENDPOINT``
    is empty — in that case spans stay in-process (NoOp) which is the current
    default. Failures to build the exporter are logged and ignored so a
    misconfigured collector can never block startup.
    """
    if not getattr(config, "OTEL_ENABLED", True):
        logger.info("OpenTelemetry disabled via OTEL_ENABLED=false.")
        return
    endpoint = getattr(config, "OTEL_EXPORTER_OTLP_ENDPOINT", "") or ""
    if not endpoint.strip():
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT unset; tracing stays in-process.")
        return
    try:
        from opentelemetry import trace as _trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {"service.name": getattr(config, "OTEL_SERVICE_NAME", "echo-gate")}
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.strip()))
        )
        _trace.set_tracer_provider(provider)
        logger.info(
            "OpenTelemetry exporting spans to OTLP endpoint %s",
            endpoint.strip(),
        )
    except Exception as exc:  # never block startup on telemetry misconfig
        logger.warning("Failed to initialize OpenTelemetry exporter: %s", exc)


def correlation_trace_id(correlation_id: str | None) -> int | None:
    """Map a correlation id string to an OTel trace-id integer, or None.

    OTel trace ids are 128-bit ints rendered as 32 hex chars. UUID-form
    correlation ids map directly (32 hex chars); shorter/opaque strings are
    hashed into the 128-bit range so every request still gets a stable id.
    """
    if not correlation_id:
        return None
    import hashlib

    raw = correlation_id.strip()
    if len(raw) == 32:
        try:
            value = int(raw, 16)
            return value or None
        except ValueError:
            pass
    hexed = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    try:
        value = int(hexed, 16)
        return value or None
    except ValueError:
        return None


@contextmanager
def trace_context_from_id(trace_id: int | None) -> Iterator[None]:
    """Attach ``trace_id`` to the current OTel context for child spans.

    Child spans started via get_tracer() inherit this id, so every span in the
    request shares the X-Correlation-ID trace id. No-op when trace_id is None
    or OTel is disabled. When the SDK is absent (NoOp provider) this still
    publishes a NonRecordingSpan context; downstream code observing
    trace.get_current_span() sees the correlation id.
    """
    if trace_id is None or not getattr(config, "OTEL_ENABLED", True):
        yield
        return
    try:
        from opentelemetry.trace import SpanContext, TraceFlags

        span_ctx = SpanContext(
            trace_id=trace_id,
            span_id=0,
            is_remote=True,
            trace_flags=TraceFlags(0x01),
        )
    except Exception:  # pragma: no cover - SDK shape change, stay NoOp
        yield
        return
    ctx = trace.set_span_in_context(trace.NonRecordingSpan(span_ctx))
    token = (
        trace.set_current_context(ctx)
        if hasattr(trace, "set_current_context")
        else None
    )
    try:
        yield
    finally:
        if token is not None and hasattr(trace, "reset_current_context"):
            trace.reset_current_context(token)


@contextmanager
def trace_span(
    name: str,
    attributes: dict[str, Any] | None = None,
    tracer: Tracer | None = None,
) -> Iterator[Span]:
    """Context manager for tracing operations with OpenTelemetry.

    Automatically records exceptions and sets span error status on failure.
    If tracing is disabled via ``config.OTEL_ENABLED=false``, yields a no-op span.
    """
    if not getattr(config, "OTEL_ENABLED", True):
        yield trace.INVALID_SPAN
        return

    active_tracer = tracer or get_tracer()
    with active_tracer.start_as_current_span(name) as span:
        if attributes and span.is_recording():
            for key, val in attributes.items():
                if val is not None:
                    span.set_attribute(key, val)

        try:
            yield span
        except BaseException as exc:
            if span.is_recording():
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR, str(exc))
            raise
        else:
            if span.is_recording():
                span.set_status(StatusCode.OK)


def set_span_attributes(span: Span, attributes: dict[str, Any]) -> None:
    """Safely apply a dictionary of attributes to an active span."""
    if not span or not span.is_recording():
        return
    for k, v in attributes.items():
        if v is not None:
            span.set_attribute(k, v)
