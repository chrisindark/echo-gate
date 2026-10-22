"""Admin CRUD endpoints for LLM quota rules.

Exposes :class:`~app.modules.llm_quota.llm_quota_rule_service.LlmQuotaRuleService`
so operators can manage per-tenant RPM/TPM limits without touching Postgres.

Every route is gated by :class:`~app.core.guards.AdminApiKeyGuard` with a
``quota:*`` scope. The guard authenticates the ``X-Admin-Key`` header and its
``AdminKeyIdentity`` is passed into each service call, because the service opens
sessions in ``system_rls_context()`` and so must never run unauthenticated.

Admin keys are created only by ``app/scripts/create_admin_key.py``; there is
intentionally no HTTP endpoint that mints one.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_llm_quota_rule_service
from app.core.guards import require_admin_scope
from app.core.responses import BaseAPIResponse
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.llm_quota.llm_quota_rule_service import LlmQuotaRuleService
from app.modules.llm_quota.llm_quota_schema import (
    LlmQuotaRuleCreate,
    LlmQuotaRuleListResponse,
    LlmQuotaRuleResponse,
    LlmQuotaRuleUpdate,
)

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/admin/llm-quota", tags=["LLM Quota (Admin)"])

# Guards, one per privilege level. Instantiated once at import so FastAPI does
# not rebuild the dependency class per request.
RequireQuotaRead = require_admin_scope("quota:read")
RequireQuotaWrite = require_admin_scope("quota:write")


@api_v1_router.post(
    "/",
    response_model=BaseAPIResponse[LlmQuotaRuleResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_quota_rule(
    data: LlmQuotaRuleCreate,
    admin: AdminKeyIdentity = Depends(RequireQuotaWrite),
    quota_rule_service: LlmQuotaRuleService = Depends(get_llm_quota_rule_service),
):
    """Create a quota rule.

    A rule with all scope fields unset (``tenant_id``/``user_id``/``provider``/
    ``model`` all null) is a global rule that applies to every tenant. Setting
    ``tenant_id`` scopes the rule to that tenant.
    """
    try:
        record = quota_rule_service.create_rule(data, admin)
        return BaseAPIResponse.success_response(
            LlmQuotaRuleResponse.model_validate(record),
            status_code=status.HTTP_201_CREATED,
        )
    except HTTPException:
        raise
    except ValueError as e:
        # Pydantic validation surfaced by the service layer.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.exception("Failed to create quota rule")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while creating the quota rule.",
        ) from e


@api_v1_router.get(
    "/",
    response_model=BaseAPIResponse[LlmQuotaRuleListResponse],
)
def list_quota_rules(
    admin: AdminKeyIdentity = Depends(RequireQuotaRead),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    tenant_id: str | None = Query(None, description="Filter by tenant"),
    provider: str | None = Query(None),
    model: str | None = Query(None),
    is_active: bool | None = Query(None),
    quota_rule_service: LlmQuotaRuleService = Depends(get_llm_quota_rule_service),
):
    """List quota rules across every tenant, with optional filters."""
    rules, total = quota_rule_service.list_rules(
        admin,
        skip=skip,
        limit=limit,
        tenant_id=tenant_id,
        provider=provider,
        model=model,
        is_active=is_active,
    )
    return BaseAPIResponse.success_response(
        LlmQuotaRuleListResponse(
            rules=[LlmQuotaRuleResponse.model_validate(rule) for rule in rules],
            total=total,
            skip=skip,
            limit=limit,
        )
    )


@api_v1_router.get(
    "/tenants",
    response_model=BaseAPIResponse[list[str]],
)
def list_quota_tenants(
    admin: AdminKeyIdentity = Depends(RequireQuotaRead),
    quota_rule_service: LlmQuotaRuleService = Depends(get_llm_quota_rule_service),
):
    """Distinct tenant IDs that have at least one quota rule.

    Global rules have a NULL tenant and are not included here.
    """
    return BaseAPIResponse.success_response(quota_rule_service.list_tenants(admin))


@api_v1_router.get(
    "/effective",
    response_model=BaseAPIResponse[dict],
)
def get_effective_limits(
    admin: AdminKeyIdentity = Depends(RequireQuotaRead),
    tenant_id: str | None = Query(None),
    provider: str | None = Query(None),
    model: str | None = Query(None),
    user: str | None = Query(None, description="User identity within the tenant"),
    quota_rule_service: LlmQuotaRuleService = Depends(get_llm_quota_rule_service),
):
    """Resolve the most restrictive limits currently applying to a scope.

    Read-only preview against the rule table; it does not consult Redis, so it
    reflects configured limits rather than live counters. When no rule matches,
    the gateway falls back to its hardcoded defaults (10 RPM / 20000 TPM).
    """
    max_rpm, max_tpm = quota_rule_service.preview_effective_limits(
        admin,
        tenant_id=tenant_id,
        provider=provider,
        model=model,
        user=user,
    )
    return BaseAPIResponse.success_response(
        {
            "max_rpm": max_rpm,
            "max_tpm": max_tpm,
            "uses_defaults": max_rpm is None and max_tpm is None,
            "default_max_rpm": 10,
            "default_max_tpm": 20000,
        }
    )


@api_v1_router.get(
    "/{rule_id}",
    response_model=BaseAPIResponse[LlmQuotaRuleResponse],
)
def get_quota_rule(
    rule_id: int,
    admin: AdminKeyIdentity = Depends(RequireQuotaRead),
    quota_rule_service: LlmQuotaRuleService = Depends(get_llm_quota_rule_service),
):
    """Fetch a single quota rule by ID."""
    record = quota_rule_service.get_rule(rule_id, admin)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quota rule with ID {rule_id} not found.",
        )
    return BaseAPIResponse.success_response(LlmQuotaRuleResponse.model_validate(record))


@api_v1_router.patch(
    "/{rule_id}",
    response_model=BaseAPIResponse[LlmQuotaRuleResponse],
)
def update_quota_rule(
    rule_id: int,
    data: LlmQuotaRuleUpdate,
    admin: AdminKeyIdentity = Depends(RequireQuotaWrite),
    quota_rule_service: LlmQuotaRuleService = Depends(get_llm_quota_rule_service),
):
    """Partially update a quota rule. Omitted fields are left unchanged."""
    record = quota_rule_service.update_rule(rule_id, data, admin)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quota rule with ID {rule_id} not found.",
        )
    return BaseAPIResponse.success_response(LlmQuotaRuleResponse.model_validate(record))


@api_v1_router.delete(
    "/{rule_id}",
    response_model=BaseAPIResponse[None],
)
def delete_quota_rule(
    rule_id: int,
    admin: AdminKeyIdentity = Depends(RequireQuotaWrite),
    quota_rule_service: LlmQuotaRuleService = Depends(get_llm_quota_rule_service),
):
    """Delete a quota rule permanently."""
    if not quota_rule_service.delete_rule(rule_id, admin):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quota rule with ID {rule_id} not found.",
        )
    return BaseAPIResponse.success_response(None)
