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
        # API key missing
        if not x_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API Key missing",
            )

        provider = None
        model = None
        user = getattr(request.state, "user", None)
        tenant_id = request.headers.get("x-tenant-id") or "anonymous"

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
