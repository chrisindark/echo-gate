import logging
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logger import correlation_id_ctx_var

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
    async def dispatch(self, request: Request, call_next):
        # Fallback to session ID from headers or tenant ID if no user ID is provided
        user = (
            request.headers.get("x-user-id")
            or request.headers.get("x-session-id")
            or request.headers.get("x-tenant-id")
            or "anonymous"
        )
        request.state.user = user

        response = await call_next(request)
        return response
