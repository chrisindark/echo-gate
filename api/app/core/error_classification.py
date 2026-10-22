import asyncio
import logging
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

PROVIDER_ERROR_CONTENT_FILTER = "provider_content_filter"
PROVIDER_ERROR_RATE_LIMIT = "provider_rate_limit"
PROVIDER_ERROR_QUOTA_EXCEEDED = "provider_quota_exceeded"
PROVIDER_ERROR_AUTH = "provider_auth_error"
PROVIDER_ERROR_UNAVAILABLE = "provider_unavailable"
PROVIDER_ERROR_TIMEOUT = "provider_timeout"
PROVIDER_ERROR_INVALID_REQUEST = "provider_invalid_request"
PROVIDER_ERROR_UNKNOWN = "provider_error"


def classify_provider_error(
    exc: BaseException, provider: str = "upstream"
) -> dict[str, Any]:
    """Classify an upstream provider exception into a normalized, structured error descriptor."""
    msg = str(exc)
    status_code = 500

    if isinstance(exc, HTTPException):
        status_code = exc.status_code
        if isinstance(exc.detail, dict):
            msg = exc.detail.get("message", str(exc.detail))
        elif exc.detail:
            msg = str(exc.detail)

    lower_msg = msg.lower()

    if (
        isinstance(exc, (asyncio.TimeoutError, TimeoutError))
        or status_code in (408, 504)
        or "timed out" in lower_msg
    ):
        return {
            "error_code": PROVIDER_ERROR_TIMEOUT,
            "provider": provider,
            "message": f"Provider '{provider}' request timed out: {msg}",
            "status_code": 504,
            "retryable": True,
        }

    if (
        "content filter" in lower_msg
        or "safety" in lower_msg
        or "blocked" in lower_msg
        or "harmful" in lower_msg
    ):
        return {
            "error_code": PROVIDER_ERROR_CONTENT_FILTER,
            "provider": provider,
            "message": f"Provider '{provider}' rejected the prompt due to content/safety filters: {msg}",
            "status_code": 400,
            "retryable": False,
        }

    if (
        "quota" in lower_msg
        or "insufficient_quota" in lower_msg
        or "billing" in lower_msg
    ):
        return {
            "error_code": PROVIDER_ERROR_QUOTA_EXCEEDED,
            "provider": provider,
            "message": f"Provider '{provider}' quota or credits exceeded: {msg}",
            "status_code": 429,
            "retryable": False,
        }

    if (
        status_code == 429
        or "rate limit" in lower_msg
        or "too many requests" in lower_msg
    ):
        return {
            "error_code": PROVIDER_ERROR_RATE_LIMIT,
            "provider": provider,
            "message": f"Provider '{provider}' rate limit reached: {msg}",
            "status_code": 429,
            "retryable": True,
        }

    if (
        status_code in (401, 403)
        or "unauthorized" in lower_msg
        or "invalid api key" in lower_msg
        or "authentication" in lower_msg
    ):
        return {
            "error_code": PROVIDER_ERROR_AUTH,
            "provider": provider,
            "message": f"Provider '{provider}' authentication failure: {msg}",
            "status_code": status_code if status_code in (401, 403) else 401,
            "retryable": False,
        }

    if (
        status_code in (500, 502, 503)
        or isinstance(exc, (ConnectionError, OSError))
        or "connection error" in lower_msg
        or "service unavailable" in lower_msg
    ):
        return {
            "error_code": PROVIDER_ERROR_UNAVAILABLE,
            "provider": provider,
            "message": f"Provider '{provider}' is temporarily unavailable ({status_code}): {msg}",
            "status_code": 503,
            "retryable": True,
        }

    if 400 <= status_code < 500:
        return {
            "error_code": PROVIDER_ERROR_INVALID_REQUEST,
            "provider": provider,
            "message": f"Provider '{provider}' rejected the request: {msg}",
            "status_code": status_code,
            "retryable": False,
        }

    return {
        "error_code": PROVIDER_ERROR_UNKNOWN,
        "provider": provider,
        "message": f"Provider '{provider}' error: {msg}",
        "status_code": status_code,
        "retryable": status_code in (500, 502, 503, 504),
    }
