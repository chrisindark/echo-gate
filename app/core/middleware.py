import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.logger import correlation_id_ctx_var

class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Check if the client sent a correlation ID, otherwise generate one
        correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        
        # Set the correlation ID in the context variable
        token = correlation_id_ctx_var.set(correlation_id)
        
        try:
            response = await call_next(request)
            # Add the correlation ID to the response headers
            response.headers["X-Correlation-Id"] = correlation_id
            return response
        finally:
            # Reset the context variable
            correlation_id_ctx_var.reset(token)

class UserMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Fallback to session ID from headers or tenant ID if no user is provided
        user = request.headers.get("x-session-id") or request.headers.get("x-tenant-id") or "anonymous"
        request.state.user = user
        
        response = await call_next(request)
        return response
