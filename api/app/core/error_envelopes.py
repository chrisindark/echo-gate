"""Provider-native error envelopes for the completion surfaces.

The SSE path already emits OpenAI-shaped errors, but non-streaming failures
returned FastAPI's ``{"detail": ...}``. SDK clients parse errors by shape, so
``/chat/completions`` now answers with the OpenAI envelope and ``/messages``
with the Anthropic one. Every other route keeps FastAPI's default body: those
surfaces are gateway-native and changing them would break existing callers.

The handlers are installed app-wide because FastAPI exception handlers are
per-app, not per-router; `surface_for_path` decides whether a request's surface
opts in. Our structured provider failures carry ``error_code`` in the
``HTTPException.detail`` dict, which is preserved as the OpenAI ``code`` field
and echoed in ``X-EchoGate-Error-Code``. Existing headers (``Retry-After``,
``X-RateLimit-*``) are forwarded untouched, and the terminal failure is labelled
``X-Gateway-Degradation: hard_down`` as the resilience contract promises.
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import (
    http_exception_handler as default_http_exception_handler,
)
from fastapi.exception_handlers import (
    request_validation_exception_handler as default_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.constants import EchoGateHeaders

Surface = Literal["openai", "anthropic"]

# Exact paths only. `/chat/completions` sub-routes (diagnostics, estimate) and
# the admin surfaces are gateway-native, so they keep `{"detail": ...}`.
OPENAI_SURFACE_PATHS = frozenset({"/api/v1/chat/completions"})
ANTHROPIC_SURFACE_PATHS = frozenset({"/api/v1/messages", "/v1/messages"})


class OpenAIErrorDetail(BaseModel):
    message: str
    type: str = Field(
        description=(
            "OpenAI error class: `invalid_request_error`, "
            "`authentication_error`, `permission_error`, `not_found_error`, "
            "`rate_limit_error`, `insufficient_quota`, or `server_error`."
        )
    )
    param: str | None = Field(
        default=None, description="Request field that caused the failure."
    )
    code: str | None = Field(
        default=None,
        description=(
            "Stable gateway/provider error code (for example "
            "`provider_unavailable`), or null for gateway validation errors."
        ),
    )


class OpenAIErrorResponse(BaseModel):
    error: OpenAIErrorDetail


class AnthropicErrorDetail(BaseModel):
    type: str = Field(
        description=(
            "Anthropic error class: `invalid_request_error`, "
            "`authentication_error`, `permission_error`, `not_found_error`, "
            "`request_too_large`, `rate_limit_error`, `api_error`, or "
            "`overloaded_error`."
        )
    )
    message: str


class AnthropicErrorResponse(BaseModel):
    type: Literal["error"] = "error"
    error: AnthropicErrorDetail


# Documented on the two completion operations. ``invalid_request_error`` is used
# for 400/422, and 5xx keeps the gateway's status rather than remapping.
_OPENAI_ERROR_RESPONSES = {
    400: "Invalid request (unsupported service or disallowed model).",
    401: "Missing, unknown, or inactive API key.",
    402: "Prepaid budget exhausted.",
    422: "Request body failed validation.",
    429: "Quota or bulkhead limit reached; retryable responses carry Retry-After.",
    500: "Unexpected gateway error.",
    503: "No provider could answer; retryable responses carry Retry-After.",
    504: "Provider or request timeout.",
}
_ANTHROPIC_ERROR_RESPONSES = {
    400: "Invalid request (unsupported service or disallowed model).",
    401: "Missing, unknown, or inactive API key.",
    402: "Prepaid budget exhausted.",
    422: "Request body failed validation.",
    429: "Quota or bulkhead limit reached; retryable responses carry Retry-After.",
    500: "Unexpected gateway error.",
    503: "Upstream overloaded or unavailable; retryable responses carry Retry-After.",
    504: "Provider or request timeout.",
}

OPENAI_ERROR_RESPONSES = {
    status: {"model": OpenAIErrorResponse, "description": description}
    for status, description in _OPENAI_ERROR_RESPONSES.items()
}
ANTHROPIC_ERROR_RESPONSES = {
    status: {"model": AnthropicErrorResponse, "description": description}
    for status, description in _ANTHROPIC_ERROR_RESPONSES.items()
}


def surface_for_path(path: str) -> Surface | None:
    """Which SDK surface (if any) owns this request path."""
    normalized = path.rstrip("/") or "/"
    if normalized in OPENAI_SURFACE_PATHS:
        return "openai"
    if normalized in ANTHROPIC_SURFACE_PATHS:
        return "anthropic"
    return None


def _openai_error_type(status_code: int) -> str:
    if status_code == 402:
        return "insufficient_quota"
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    if status_code == 404:
        return "not_found_error"
    if status_code == 429:
        return "rate_limit_error"
    if status_code >= 500:
        return "server_error"
    return "invalid_request_error"


def _anthropic_error_type(status_code: int) -> str:
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    if status_code == 404:
        return "not_found_error"
    if status_code == 413:
        return "request_too_large"
    if status_code == 429:
        return "rate_limit_error"
    if status_code == 503:
        return "overloaded_error"
    if status_code >= 500:
        return "api_error"
    return "invalid_request_error"


def _message_and_code(detail: object) -> tuple[str, str | None, str | None]:
    """Normalize ``HTTPException.detail`` into (message, code, param)."""
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail or "Request failed.")
        code = detail.get("error_code")
        param = detail.get("param")
        return (
            message,
            str(code) if code else None,
            str(param) if param else None,
        )
    if isinstance(detail, (list, tuple)):
        joined = "; ".join(str(item) for item in detail)
        return joined or "Request failed.", None, None
    if detail is None:
        return "Request failed.", None, None
    return str(detail), None, None


def _validation_problem(exc: RequestValidationError) -> tuple[str, str | None]:
    """First validation error as (message, param path)."""
    errors = exc.errors()
    if not errors:
        return "Request validation failed.", None
    first = errors[0]
    loc = [str(part) for part in first.get("loc", ()) if part != "body"]
    param = ".".join(loc) or None
    message = str(first.get("msg") or "Invalid request")
    if param:
        message = f"{param}: {message}"
    if len(errors) > 1:
        message = f"{message} (+{len(errors) - 1} more validation error(s))"
    return message, param


def _error_response(
    surface: Surface,
    status_code: int,
    message: str,
    *,
    code: str | None = None,
    param: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    if surface == "openai":
        body: BaseModel = OpenAIErrorResponse(
            error=OpenAIErrorDetail(
                message=message,
                type=_openai_error_type(status_code),
                param=param,
                code=code,
            )
        )
    else:
        body = AnthropicErrorResponse(
            error=AnthropicErrorDetail(
                type=_anthropic_error_type(status_code),
                message=message,
            )
        )

    response_headers = dict(headers or {})
    # The resilience contract promises a degradation state on every completion
    # response; an error response never got past the router, so it is terminal.
    response_headers.setdefault(
        EchoGateHeaders.GATEWAY_DEGRADATION,
        "hard_down" if status_code >= 500 else "none",
    )
    if code:
        response_headers.setdefault(EchoGateHeaders.ERROR_CODE, code)

    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        headers=response_headers,
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    surface = surface_for_path(request.url.path)
    if surface is None:
        return await default_http_exception_handler(request, exc)

    message, code, param = _message_and_code(exc.detail)
    return _error_response(
        surface,
        exc.status_code,
        message,
        code=code,
        param=param,
        headers=dict(exc.headers or {}),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    surface = surface_for_path(request.url.path)
    if surface is None:
        return await default_validation_exception_handler(request, exc)

    message, param = _validation_problem(exc)
    return _error_response(surface, 422, message, param=param)


def install_error_envelopes(app: FastAPI) -> None:
    """Register the per-surface handlers on ``app``.

    FastAPI resolves handlers per exception class, so registering
    ``HTTPException`` also covers its subclasses (circuit breaker, bulkhead).
    Paths outside the two SDK surfaces fall through to FastAPI's defaults.
    """
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
