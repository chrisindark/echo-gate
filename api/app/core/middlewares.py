import asyncio
import logging
import time
import uuid

from fastapi import Request
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logger import correlation_id_ctx_var
from app.core.rls import ANONYMOUS_TENANT, rls_context

logger = logging.getLogger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Standard FastAPI / Starlette middlewares that:
    1. Reads or generates request ID and correlation ID from incoming HTTP headers.
    2. Sets correlation_id_ctx_var for the lifecycle of the request.
    3. Appends X-Correlation-ID and X-Request-ID headers to the response.
    4. Logs structured access information with HTTP status and latency.
    """

    async def dispatch(self, request: Request, call_next):
        # Extract request ID and correlation ID from headers or generate
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        correlation_id = request.headers.get("x-correlation-id") or request_id

        # Set the correlation ID in the context variable
        token = correlation_id_ctx_var.set(correlation_id)

        start_time = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            end_time = time.perf_counter()
            process_time = end_time - start_time
            duration_ms = round(process_time * 1000, 2)

            # Extract client IP (handle proxy forwarded headers first)
            forwarded_for = request.headers.get("x-forwarded-for")
            if forwarded_for:
                client_ip = forwarded_for.split(",")[0].strip()
            elif request.client:
                client_ip = request.client.host
            else:
                client_ip = "unknown"

            method = request.method
            path = request.url.path
            if request.url.query:
                path = f"{path}?{request.url.query}"
            http_version = request.scope.get("http_version", "1.1")

            logger.info(
                f'{client_ip} - "{method} {path} HTTP/{http_version}" {status_code} '
                f"({process_time:.3f}s) [req_id={request_id}]",
                extra={
                    "http_method": method,
                    "http_path": path,
                    "http_status": status_code,
                    "duration_ms": duration_ms,
                    "client_ip": client_ip,
                    "http_version": http_version,
                    "request_id": request_id,
                },
            )
            correlation_id_ctx_var.reset(token)


class UserMiddleware(BaseHTTPMiddleware):
    """Publish the resolved caller identity as ``request.state.user``.

    ``TenantContextMiddleware`` runs first and is the only source of identity:
    an ``X-API-Key`` resolved server-side to a tenant/user, else ``anonymous``.
    This middleware used to read ``X-User-ID``/``X-Session-ID``/``X-Tenant-ID``
    itself, which let a caller name themselves freely even while presenting a
    valid key.
    """

    async def dispatch(self, request: Request, call_next):
        request.state.user = getattr(request.state, "user_id", None) or ANONYMOUS_TENANT

        response = await call_next(request)
        return response


class TenantContextMiddleware:
    """Establish the Postgres RLS tenant context for each request.

    Written as a pure ASGI middleware (not ``BaseHTTPMiddleware``) so the
    contextvars set here are visible to the endpoint and its thread-pool work,
    which is what the RLS GUC hook in ``app.core.rls`` reads.

    Resolution order:
    1. ``X-API-Key`` resolved server-side to a tenant/user (trusted).
    2. ``anonymous`` tenant.

    ``X-Tenant-ID``/``X-User-ID`` are deliberately **not** read here. They are
    caller-supplied, so honouring them meant anyone who could reach the gateway
    could pick the tenant its request ran as — and, combined with the request
    body, read another tenant's cache. ``X-API-Key`` is now the only credential
    that establishes identity.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        api_key = Headers(scope=scope).get("x-api-key")
        tenant_id: str | None = None
        user_id: str | None = None

        if api_key:
            # Imported lazily so the middleware module stays import-light.
            from app.modules.api_keys.api_keys_service import api_key_service

            identity = await asyncio.to_thread(api_key_service.resolve_api_key, api_key)
            if identity is not None:
                tenant_id = identity.tenant_id
                user_id = identity.user_id

        state = scope.setdefault("state", {})
        state["tenant_id"] = tenant_id or ANONYMOUS_TENANT
        state["user_id"] = user_id

        with rls_context(tenant_id=tenant_id, user_id=user_id):
            await self.app(scope, receive, send)
