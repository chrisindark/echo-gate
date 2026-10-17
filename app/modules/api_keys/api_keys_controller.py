import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_api_key_service
from app.core.responses import BaseAPIResponse
from app.modules.api_keys.api_keys_schema import (
    ApiKeyCreate,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
)
from app.modules.api_keys.api_keys_service import ApiKeyService

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/admin/api-keys", tags=["API Keys"])


@api_v1_router.post(
    "/",
    response_model=BaseAPIResponse[ApiKeyCreatedResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_api_key(
    data: ApiKeyCreate,
    api_key_service: ApiKeyService = Depends(get_api_key_service),
):
    """Create an API key bound to a tenant/user.

    The plaintext key is returned exactly once in this response; only its hash is
    persisted. Send it as the ``X-API-Key`` header to authenticate requests and
    establish the Postgres RLS tenant context.
    """
    try:
        record, plaintext = api_key_service.create_api_key(data)
        payload = ApiKeyCreatedResponse.model_validate(record)
        payload.api_key = plaintext
        return BaseAPIResponse.success_response(payload)
    except Exception as e:
        logger.exception("Failed to create API key")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while creating the API key.",
        ) from e


@api_v1_router.get(
    "/",
    response_model=BaseAPIResponse[list[ApiKeyResponse]],
)
def list_api_keys(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    api_key_service: ApiKeyService = Depends(get_api_key_service),
):
    """List API keys (metadata only; plaintext keys are never returned)."""
    keys = api_key_service.list_api_keys(skip=skip, limit=limit)
    return BaseAPIResponse.success_response(
        [ApiKeyResponse.model_validate(key) for key in keys]
    )


@api_v1_router.delete(
    "/{key_id}",
    response_model=BaseAPIResponse[None],
)
def revoke_api_key(
    key_id: int,
    api_key_service: ApiKeyService = Depends(get_api_key_service),
):
    """Revoke an API key. The key remains visible but can no longer authenticate."""
    if not api_key_service.revoke_api_key(key_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key with ID {key_id} not found.",
        )
    return BaseAPIResponse.success_response(None)
