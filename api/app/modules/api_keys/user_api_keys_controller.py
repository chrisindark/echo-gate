import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_api_key_service
from app.core.guards import UserSessionIdentity, user_auth_guard
from app.core.responses import BaseAPIResponse
from app.modules.api_keys.api_keys_schema import (
    ApiKeyCreate,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    ApiKeyRotateRequest,
    ApiKeyRotateResponse,
    UserApiKeyCreate,
    UserProfileResponse,
)
from app.modules.api_keys.api_keys_service import ApiKeyService

logger = logging.getLogger(__name__)

user_api_v1_router = APIRouter(prefix="/api/v1/user", tags=["User Management"])


@user_api_v1_router.get(
    "/me",
    response_model=BaseAPIResponse[UserProfileResponse],
    summary="Get authenticated user session profile",
)
def get_user_profile(
    user: UserSessionIdentity = Depends(user_auth_guard),
):
    """Retrieve the authenticated user's profile and resolved personal tenant ID."""
    return BaseAPIResponse.success_response(
        UserProfileResponse(
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            email=user.email,
            role=user.role,
        )
    )


@user_api_v1_router.get(
    "/api-keys",
    response_model=BaseAPIResponse[list[ApiKeyResponse]],
    summary="List API keys for current tenant",
)
def list_user_api_keys(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    user: UserSessionIdentity = Depends(user_auth_guard),
    api_key_service: ApiKeyService = Depends(get_api_key_service),
):
    """List API keys belonging to the authenticated user's tenant (metadata only)."""
    keys = api_key_service.list_api_keys_for_tenant(
        tenant_id=user.tenant_id, skip=skip, limit=limit
    )
    return BaseAPIResponse.success_response(
        [ApiKeyResponse.model_validate(key) for key in keys]
    )


@user_api_v1_router.post(
    "/api-keys",
    response_model=BaseAPIResponse[ApiKeyCreatedResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new API key for current tenant",
)
def create_user_api_key(
    data: UserApiKeyCreate,
    user: UserSessionIdentity = Depends(user_auth_guard),
    api_key_service: ApiKeyService = Depends(get_api_key_service),
):
    """Create a new API key for the authenticated user's tenant.

    The plaintext key is returned exactly once in this response. Save and copy it
    immediately; only its SHA-256 hash is persisted in the database.
    """
    try:
        record, plaintext = api_key_service.create_api_key(
            ApiKeyCreate(
                name=data.name,
                tenant_id=user.tenant_id,
                user_id=user.user_id,
            )
        )
        base = ApiKeyResponse.model_validate(record)
        payload = ApiKeyCreatedResponse(**base.model_dump(), api_key=plaintext)
        return BaseAPIResponse.success_response(payload)
    except Exception as e:
        logger.exception("Failed to create user API key")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while creating the API key.",
        ) from e


@user_api_v1_router.delete(
    "/api-keys/{key_id}",
    response_model=BaseAPIResponse[None],
    summary="Revoke an API key for current tenant",
)
def revoke_user_api_key(
    key_id: int,
    user: UserSessionIdentity = Depends(user_auth_guard),
    api_key_service: ApiKeyService = Depends(get_api_key_service),
):
    """Revoke an API key belonging to the user's tenant."""
    if not api_key_service.revoke_api_key_for_tenant(
        key_id=key_id, tenant_id=user.tenant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key with ID {key_id} not found.",
        )
    return BaseAPIResponse.success_response(None)


@user_api_v1_router.post(
    "/api-keys/{key_id}/rotate",
    response_model=BaseAPIResponse[ApiKeyRotateResponse],
    summary="Rotate an API key for current tenant",
)
def rotate_user_api_key(
    key_id: int,
    data: ApiKeyRotateRequest = ApiKeyRotateRequest(),
    user: UserSessionIdentity = Depends(user_auth_guard),
    api_key_service: ApiKeyService = Depends(get_api_key_service),
):
    """Rotate an API key: generates a new key and sets the old key's expiry."""
    result = api_key_service.rotate_api_key_for_tenant(
        key_id=key_id, tenant_id=user.tenant_id, grace_hours=data.grace_hours
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active API key with ID {key_id} not found.",
        )
    new_record, plaintext, old_record = result
    base = ApiKeyResponse.model_validate(new_record)
    new_key_payload = ApiKeyCreatedResponse(**base.model_dump(), api_key=plaintext)
    expiring_key_payload = ApiKeyResponse.model_validate(old_record)
    return BaseAPIResponse.success_response(
        ApiKeyRotateResponse(
            new_key=new_key_payload,
            expiring_key=expiring_key_payload,
        )
    )
