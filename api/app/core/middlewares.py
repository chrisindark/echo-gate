import asyncio
import logging
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders

from app.core.constants import EchoGateHeaders
from app.core.identity import (
    API_KEY_HEADER,
    AUTHORIZATION_HEADER,
    api_key_from_headers,
)
from app.core.logger import correlation_id_ctx_var
from app.core.rls import ANONYMOUS_TENANT, rls_context
from app.core.telemetry import correlation_trace_id, trace_context_from_id, trace_span

logger = logging.getLogger(__name__)


class CorrelationIdMiddleware:
    """
    Pure ASGI middleware that:
    1. Reads or generates request ID and correlation ID from incoming HTTP headers.
    2. Sets correlation_id_ctx_var for the lifecycle of the request (including streaming).
    3. Appends X-Correlation-ID and X-Request-ID headers to the response.
    4. Logs structured access information with HTTP status and latency.

    Implemented as pure ASGI (rather than ``BaseHTTPMiddleware``) so
    ``StreamingResponse`` chunks flow directly without Starlette's task-group
    stream wrapping or buffering.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get("x-request-id") or str(uuid.uuid4())
        correlation_id = headers.get("x-correlation-id") or request_id
        trace_id = correlation_trace_id(correlation_id)

        if trace_id is not None:
            scope["trace_id"] = trace_id
            scope["otel_trace_id"] = format(trace_id, "032x")
            scope["correlation_id"] = correlation_id

        token = correlation_id_ctx_var.set(correlation_id)

        start_time = time.perf_counter()
        status_code = 500
        method = scope.get("method", "GET")
        raw_path = scope.get("path", "/")
        query_bytes = scope.get("query_string", b"")
        query_str = query_bytes.decode("latin-1") if query_bytes else ""
        full_path = f"{raw_path}?{query_str}" if query_str else raw_path

        span_attrs = {
            "http.method": method,
            "http.route": raw_path,
            "correlation_id": correlation_id,
        }

        async def send_wrapper(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                resp_headers = MutableHeaders(scope=message)
                resp_headers[EchoGateHeaders.CORRELATION_ID] = correlation_id
                resp_headers[EchoGateHeaders.REQUEST_ID] = request_id
            await send(message)

        with (
            trace_context_from_id(trace_id),
            trace_span("http.server.request", span_attrs),
        ):
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                end_time = time.perf_counter()
                process_time = end_time - start_time
                duration_ms = round(process_time * 1000, 2)

                forwarded_for = headers.get("x-forwarded-for")
                client = scope.get("client")
                if forwarded_for:
                    client_ip = forwarded_for.split(",")[0].strip()
                elif client:
                    client_ip = client[0]
                else:
                    client_ip = "unknown"

                http_version = scope.get("http_version", "1.1")

                logger.info(
                    f'{client_ip} - "{method} {full_path} HTTP/{http_version}" {status_code} '
                    f"({process_time:.3f}s) [req_id={request_id}]",
                    extra={
                        "http_method": method,
                        "http_path": full_path,
                        "http_status": status_code,
                        "duration_ms": duration_ms,
                        "client_ip": client_ip,
                        "http_version": http_version,
                        "request_id": request_id,
                    },
                )
                correlation_id_ctx_var.reset(token)


class UserMiddleware:
    """Publish the resolved caller identity as ``request.state.user``.

    ``TenantContextMiddleware`` runs first and is the only source of identity:
    an ``X-API-Key`` or ``Authorization: Bearer`` credential resolved
    server-side to a tenant/user, else ``anonymous``.

    Implemented as pure ASGI (rather than ``BaseHTTPMiddleware``) so
    ``StreamingResponse`` passes through directly without stream buffering.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        state["user"] = state.get("user_id") or ANONYMOUS_TENANT
        await self.app(scope, receive, send)


class TenantContextMiddleware:
    """Establish the Postgres RLS tenant context for each request.

    Written as a pure ASGI middleware (not ``BaseHTTPMiddleware``) so the
    contextvars set here are visible to the endpoint and its thread-pool work,
    which is what the RLS GUC hook in ``app.core.rls`` reads.

    Resolution order:
    1. ``X-API-Key`` resolved server-side to a tenant/user (trusted).
    2. ``Authorization: Bearer <key>`` resolved the same way, for OpenAI SDK
       compatibility. An explicit ``X-API-Key`` wins if both are present.
    3. ``anonymous`` tenant.

    ``X-Tenant-ID``/``X-User-ID`` are deliberately **not** read here. They are
    caller-supplied, so honouring them meant anyone who could reach the gateway
    could pick the tenant its request ran as — and, combined with the request
    body, read another tenant's cache. Only the two server-resolved credential
    headers establish identity.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        api_key = api_key_from_headers(headers)
        tenant_id: str | None = None
        user_id: str | None = None

        if api_key:
            # IdentityProvider seam (OSS: ApiKeyIdentityProvider). A cloud
            # overlay registers OidcIdentityProvider here; the resolution
            # order and trust model are unchanged. Pass the original headers
            # through so a cloud provider can read a bearer JWT itself.
            from app.core.identity import default_provider

            provider = default_provider()

            def _resolve() -> None:
                nonlocal tenant_id, user_id
                identity = provider.resolve(
                    {
                        API_KEY_HEADER: headers.get(API_KEY_HEADER) or "",
                        AUTHORIZATION_HEADER: headers.get(AUTHORIZATION_HEADER) or "",
                    }
                )
                if identity is not None:
                    tenant_id = identity.tenant_id
                    user_id = identity.user_id

            await asyncio.to_thread(_resolve)

        state = scope.setdefault("state", {})
        state["tenant_id"] = tenant_id or ANONYMOUS_TENANT
        state["user_id"] = user_id

        with rls_context(tenant_id=tenant_id, user_id=user_id):
            await self.app(scope, receive, send)
