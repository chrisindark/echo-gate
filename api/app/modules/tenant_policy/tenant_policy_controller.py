"""Admin CRUD endpoints for per-tenant privacy & cache policies.

Every route requires an authenticated ``X-Admin-Key`` with ``policy:read`` or
``policy:write`` (or ``admin:*`` wildcard). Regular tenant API keys are
strictly rejected so callers cannot view or weaken org-wide restrictions.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_tenant_policy_service
from app.core.guards import require_admin_scope
from app.core.responses import BaseAPIResponse
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.tenant_policy.tenant_policy_schema import (
    TenantPrivacyPolicyListResponse,
    TenantPrivacyPolicyResponse,
    TenantPrivacyPolicyUpsert,
)
from app.modules.tenant_policy.tenant_policy_service import TenantPolicyService

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(
    prefix="/api/v1/admin/tenant-policies",
    tags=["Tenant Privacy Policy (Admin)"],
)

RequirePolicyRead = require_admin_scope("policy:read")
RequirePolicyWrite = require_admin_scope("policy:write")


@api_v1_router.get(
    "/",
    response_model=BaseAPIResponse[TenantPrivacyPolicyListResponse],
)
def list_tenant_policies(
    admin: AdminKeyIdentity = Depends(RequirePolicyRead),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    tenant_id: str | None = Query(None, description="Filter by tenant"),
    is_active: bool | None = Query(None),
    policy_service: TenantPolicyService = Depends(get_tenant_policy_service),
):
    """List configured per-tenant privacy and cache policies."""
    policies, total = policy_service.list_policies(
        admin,
        skip=skip,
        limit=limit,
        tenant_id=tenant_id,
        is_active=is_active,
    )
    return BaseAPIResponse.success_response(
        TenantPrivacyPolicyListResponse(
            policies=[TenantPrivacyPolicyResponse.model_validate(p) for p in policies],
            total=total,
            skip=skip,
            limit=limit,
        )
    )


@api_v1_router.get(
    "/tenants",
    response_model=BaseAPIResponse[list[str]],
)
def list_policy_tenants(
    admin: AdminKeyIdentity = Depends(RequirePolicyRead),
    policy_service: TenantPolicyService = Depends(get_tenant_policy_service),
):
    """List distinct tenant IDs known to the gateway for admin selection."""
    return BaseAPIResponse.success_response(policy_service.list_tenants(admin))


@api_v1_router.get(
    "/{tenant_id}",
    response_model=BaseAPIResponse[TenantPrivacyPolicyResponse],
)
def get_tenant_policy(
    tenant_id: str,
    admin: AdminKeyIdentity = Depends(RequirePolicyRead),
    policy_service: TenantPolicyService = Depends(get_tenant_policy_service),
):
    """Fetch the privacy & cache restriction configured for ``tenant_id``."""
    record = policy_service.get_policy(tenant_id, admin)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No privacy policy configured for tenant '{tenant_id}'.",
        )
    return BaseAPIResponse.success_response(
        TenantPrivacyPolicyResponse.model_validate(record)
    )


@api_v1_router.put(
    "/{tenant_id}",
    response_model=BaseAPIResponse[TenantPrivacyPolicyResponse],
)
def upsert_tenant_policy(
    tenant_id: str,
    data: TenantPrivacyPolicyUpsert,
    admin: AdminKeyIdentity = Depends(RequirePolicyWrite),
    policy_service: TenantPolicyService = Depends(get_tenant_policy_service),
):
    """Create or update the privacy & cache restriction for ``tenant_id``."""
    try:
        record = policy_service.upsert_policy(tenant_id, data, admin)
        return BaseAPIResponse.success_response(
            TenantPrivacyPolicyResponse.model_validate(record)
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.exception("Failed to upsert tenant privacy policy")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while saving the tenant privacy policy.",
        ) from e


@api_v1_router.delete(
    "/{tenant_id}",
    response_model=BaseAPIResponse[dict[str, bool]],
)
def delete_tenant_policy(
    tenant_id: str,
    admin: AdminKeyIdentity = Depends(RequirePolicyWrite),
    policy_service: TenantPolicyService = Depends(get_tenant_policy_service),
):
    """Delete ``tenant_id``'s custom restriction, reverting it to gateway defaults."""
    deleted = policy_service.delete_policy(tenant_id, admin)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No privacy policy configured for tenant '{tenant_id}'.",
        )
    return BaseAPIResponse.success_response({"deleted": True})
