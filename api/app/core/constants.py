"""Centralized constants for Echo Gate.

This module houses HTTP headers, provider identifiers, cache layers/modes,
degradation states, standard error codes, Redis key prefixes, and default
versioning parameters.

Model and cache key versioning tags (``EMBEDDING_VERSION``, ``CACHE_KEY_VERSION``,
``PROMPT_VERSION``, ``INTENT_PROMPT_VERSION``) are config-driven via environment
variables, with dynamic module-level fallbacks provided here for backward
compatibility.
"""

from typing import Any

from app.modules.qdrant.qdrant_schema import CacheScope

# =====================================================================
# Default Versioning Tags (Config-Driven, Unified "vN" Convention)
# =====================================================================
DEFAULT_EMBEDDING_VERSION = "v1"
DEFAULT_SPARSE_EMBEDDING_VERSION = "v1"
DEFAULT_CACHE_KEY_VERSION = "v1"
DEFAULT_PROMPT_VERSION = "v1"
DEFAULT_INTENT_PROMPT_VERSION = "v1"
DEFAULT_INTENT_CLASSIFIER_VERSION = "v1"
DEFAULT_CORE_QUERY_EXTRACTOR_VERSION = "v1"
DEFAULT_RERANKER_VERSION = "v1"
DEFAULT_JUDGE_PROMPT_VERSION = "v1"


# =====================================================================
# HTTP Custom Headers
# =====================================================================
class EchoGateHeaders:
    """Standard HTTP headers used across Echo Gate request/response cycles."""

    API_KEY = "X-API-Key"
    ADMIN_KEY = "X-Admin-Key"
    CORRELATION_ID = "X-Correlation-ID"
    REQUEST_ID = "X-Request-ID"
    GATEWAY_DEGRADATION = "X-Gateway-Degradation"
    TIMEOUT_BUDGET_MS = "X-Timeout-Budget-Ms"
    SESSION_ID = "X-Session-ID"
    CONVERSATION_ID = "X-Conversation-ID"
    CACHE = "X-Cache"
    CACHE_PREFER = "X-Cache-Prefer"
    RATE_LIMIT_LIMIT_REQUESTS = "X-RateLimit-Limit-Requests"
    RATE_LIMIT_REMAINING_REQUESTS = "X-RateLimit-Remaining-Requests"
    RATE_LIMIT_RESET_REQUESTS = "X-RateLimit-Reset-Requests"
    RATE_LIMIT_LIMIT_TOKENS = "X-RateLimit-Limit-Tokens"
    RATE_LIMIT_REMAINING_TOKENS = "X-RateLimit-Remaining-Tokens"
    RATE_LIMIT_RESET_TOKENS = "X-RateLimit-Reset-Tokens"
    CIRCUIT_BREAKER = "X-EchoGate-Circuit-Breaker"
    BULKHEAD = "X-EchoGate-Bulkhead"
    MODEL_RESOLVED = "X-EchoGate-Model-Resolved"
    PROVIDER_RESOLVED = "X-EchoGate-Provider-Resolved"
    ERROR_CODE = "X-EchoGate-Error-Code"
    RETRY_AFTER = "Retry-After"
    ACCEL_BUFFERING = "X-Accel-Buffering"

    EXPOSED_CORS_HEADERS = (
        CACHE,
        CORRELATION_ID,
        REQUEST_ID,
        GATEWAY_DEGRADATION,
        RATE_LIMIT_LIMIT_REQUESTS,
        RATE_LIMIT_REMAINING_REQUESTS,
        RATE_LIMIT_RESET_REQUESTS,
        RATE_LIMIT_LIMIT_TOKENS,
        RATE_LIMIT_REMAINING_TOKENS,
        RATE_LIMIT_RESET_TOKENS,
        CIRCUIT_BREAKER,
        BULKHEAD,
        MODEL_RESOLVED,
        PROVIDER_RESOLVED,
        ERROR_CODE,
        RETRY_AFTER,
    )


# =====================================================================
# Upstream LLM Providers
# =====================================================================
class ProviderName:
    """Canonical provider adapter identifiers."""

    GOOGLE_GENAI = "google-genai"
    OPENAI = "openai"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    CLAUDE = "claude"
    OLLAMA = "ollama"

    ALL = (
        GOOGLE_GENAI,
        OPENAI,
        GROQ,
        OPENROUTER,
        CLAUDE,
        OLLAMA,
    )


# =====================================================================
# Cache Layers and Serving Modes
# =====================================================================
class CacheLayer:
    """Identifiers for each cache layer in the gateway."""

    REDIS = "redis"
    QDRANT_EXACT = "qdrant_exact"
    QDRANT_SEMANTIC = "qdrant_semantic"

    ALL = (REDIS, QDRANT_EXACT, QDRANT_SEMANTIC)


class CacheMode:
    """Serving modes for the cache pipeline."""

    DISABLED = "disabled"
    SHADOW = "shadow"
    ACTIVE = "active"

    ALL = (DISABLED, SHADOW, ACTIVE)


# =====================================================================
# Cache Degradation States
# =====================================================================
class DegradationState:
    """Cache subsystem degradation states exposed in response headers."""

    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"

    ALL = (OPERATIONAL, DEGRADED, UNAVAILABLE)


# =====================================================================
# Standard Gateway Error Codes
# =====================================================================
class ErrorCode:
    """Structured error codes returned in API problem details."""

    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    BULKHEAD_CAPACITY_EXCEEDED = "bulkhead_capacity_exceeded"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    PROVIDER_QUOTA_EXCEEDED = "provider_quota_exceeded"
    PROVIDER_AUTH_ERROR = "provider_auth_error"
    PROVIDER_CONTENT_FILTER = "provider_content_filter"
    PROVIDER_INVALID_REQUEST = "provider_invalid_request"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    ALL_PROVIDERS_EXHAUSTED = "all_providers_exhausted"
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_ACCESS_DENIED = "model_access_denied"
    TIMEOUT_BUDGET_EXCEEDED = "timeout_budget_exceeded"
    DATABASE_UNAVAILABLE = "database_unavailable"
    REDIS_UNAVAILABLE = "redis_unavailable"
    QDRANT_UNAVAILABLE = "qdrant_unavailable"
    INTERNAL_ERROR = "internal_error"


# =====================================================================
# Redis Key Prefixes
# =====================================================================
class RedisKeyPrefix:
    """Namespaces for Redis keys."""

    RATE_LIMIT = "ratelimit:"
    QUOTA_RULES = "quota_rules:"
    CIRCUIT_BREAKER = "circuit_breaker:"


# =====================================================================
# Tenant & System Identifiers
# =====================================================================
ANONYMOUS_TENANT_ID = "anonymous"
SYSTEM_TENANT_ID = "system"
CALLER_SERVICE_NAME = "echo-gate-api"
MOCK_RESPONSE_TEXT = "This is a mock response from the LLM Gateway."

# String Prefixes & Identifiers
EMBEDDING_QUERY_PREFIX = ""
EMBEDDING_DOCUMENT_PREFIX = ""

# Magic Numbers & Mappings
CACHE_NEGATIVE_TTL_OFFSET = -1000

# `X-Cache-Prefer: instant` asks for a cache hit as one JSON body instead of SSE
# playback, so the client can skip the stream parser entirely.
CACHE_PREFER_INSTANT = "instant"

SCOPE_HIERARCHY = {
    CacheScope.GLOBAL.value: 4,
    CacheScope.TENANT.value: 3,
    CacheScope.USER.value: 2,
    CacheScope.SESSION.value: 1,
    CacheScope.CONVERSATION.value: 0,
}


# =====================================================================
# Dynamic Module Attributes for Config-Driven Versions
# =====================================================================
_CONFIG_DRIVEN_VERSIONS = {
    "EMBEDDING_VERSION": "EMBEDDING_VERSION",
    "SPARSE_EMBEDDING_VERSION": "SPARSE_EMBEDDING_VERSION",
    "CACHE_KEY_VERSION": "CACHE_KEY_VERSION",
    "PROMPT_VERSION": "PROMPT_VERSION",
    "INTENT_PROMPT_VERSION": "INTENT_PROMPT_VERSION",
    "INTENT_CLASSIFIER_VERSION": "INTENT_CLASSIFIER_VERSION",
    "CORE_QUERY_EXTRACTOR_VERSION": "CORE_QUERY_EXTRACTOR_VERSION",
    "RERANKER_VERSION": "RERANKER_VERSION",
    "JUDGE_PROMPT_VERSION": "JUDGE_PROMPT_VERSION",
}


def __getattr__(name: str) -> Any:
    """Resolve versioning constants dynamically from Config so environment overrides apply."""
    if name in _CONFIG_DRIVEN_VERSIONS:
        from app.core.config import config

        return getattr(config, _CONFIG_DRIVEN_VERSIONS[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
