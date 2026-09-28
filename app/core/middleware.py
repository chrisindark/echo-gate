import logging
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logger import correlation_id_ctx_var

logger = logging.getLogger(__name__)


class CorrelationIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        # Extract correlation ID from headers or generate one
        correlation_id = ""
        for name, value in scope.get("headers", []):
            if name.lower() == b"x-correlation-id":
                correlation_id = value.decode("latin1")
                break

        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        # Set the correlation ID in the context variable
        correlation_id_ctx_var.set(correlation_id)
        
        start_time = time.time()
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
                # Add the correlation ID to the response headers
                headers = message.get("headers", [])
                headers.append((b"x-correlation-id", correlation_id.encode("latin1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope["type"] == "http":
                process_time = time.time() - start_time
                client = scope.get("client")
                client_ip = f"{client[0]}:{client[1]}" if client else "unknown"
                method = scope.get("method", "")
                path = scope.get("path", "")
                http_version = scope.get("http_version", "1.1")
                
                logger.info(
                    f'{client_ip} - "{method} {path} HTTP/{http_version}" {status_code} '
                    f'({process_time:.3f}s)'
                )


class UserMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Fallback to session ID from headers or tenant ID if no user is provided
        user = (
            request.headers.get("x-session-id")
            or request.headers.get("x-tenant-id")
            or "anonymous"
        )
        request.state.user = user

        response = await call_next(request)
        return response
