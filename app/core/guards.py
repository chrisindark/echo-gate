import json
import logging

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.dependencies import get_llm_quota_service
from app.modules.llm_quota.llm_quota_service import LlmQuotaService

logger = logging.getLogger(__name__)


class LlmQuotaGuard:
    """
    Can be used as a route dependency.
    """

    async def __call__(
        self,
        request: Request,
        x_api_key: str | None = Header(default=None),
        llm_quota_service: LlmQuotaService = Depends(get_llm_quota_service),
    ):
        provider = None
        model = None
        # Identity is established by TenantContextMiddleware: an API key resolved
        # to a tenant/user, or trusted internal headers. Fall back to headers so
        # the guard keeps working if the middleware is not installed.
        user = getattr(request.state, "user_id", None) or getattr(
            request.state, "user", None
        )
        tenant_id = (
            getattr(request.state, "tenant_id", None)
            or request.headers.get("x-tenant-id")
            or "anonymous"
        )

        try:
            # Safely read body. FastAPI caches request.body() so it's safe to read multiple times
            body_bytes = await request.body()
            if body_bytes:
                body = json.loads(body_bytes)
                provider = body.get("service_name")
                model = body.get("model")
                if body.get("user"):
                    user = body.get("user")
        except Exception:
            logger.exception("Error reading request body")

        # Check quota
        await llm_quota_service.check_quota(
            api_key=x_api_key,
            provider=provider,
            model=model,
            user=user,
            tenant_id=tenant_id,
        )

        # Attach API key to request state
        request.state.api_key = x_api_key

        return True


# Initialize a singleton instance for Depends
llm_quota_guard = LlmQuotaGuard()


class ApiKeyAuthGuard:
    """
    Route dependency ensuring the request includes a valid X-API-Key.
    """

    async def __call__(
        self,
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> bool:
        if not x_api_key:
            tenant_id = request.headers.get("x-tenant-id")
            if tenant_id and tenant_id != "anonymous":
                return True
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API Key missing. Provide a valid X-API-Key header.",
            )

        import asyncio

        from app.modules.api_keys.api_keys_service import api_key_service

        identity = await asyncio.to_thread(api_key_service.resolve_api_key, x_api_key)
        if identity is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or inactive API Key.",
            )

        request.state.api_key = x_api_key
        request.state.tenant_id = identity.tenant_id
        request.state.user_id = identity.user_id
        return True


api_key_auth_guard = ApiKeyAuthGuard()
