"""Pydantic schemas and immutable snapshot types for per-tenant privacy policies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PiiScrubbingPolicyMode = Literal["inherit", "enforced", "disabled"]
GuardrailsPolicyMode = Literal["inherit", "off", "inject", "block"]


class TenantPrivacyPolicyUpsert(BaseModel):
    """Admin payload to create or update a tenant's privacy & cache restriction."""

    semantic_cache_enabled: bool = Field(
        default=True,
        description=(
            "When false, completely disables semantic matching and exact caching "
            "(Redis and Qdrant) for every request in this tenant."
        ),
    )
    zero_retention: bool = Field(
        default=False,
        description=(
            "When true, enforces Zero-Retention Mode across the tenant: skips "
            "Postgres logging and cache reads/writes while keeping quota checks active."
        ),
    )
    pii_scrubbing_mode: PiiScrubbingPolicyMode = Field(
        default="inherit",
        description=(
            "Controls Stage 0 PII scrubbing: 'enforced' mandates redaction, "
            "'disabled' disables scrubbing for latency/accuracy, 'inherit' defers "
            "to global defaults and per-request headers."
        ),
    )
    guardrails_mode: GuardrailsPolicyMode = Field(
        default="inherit",
        description=(
            "Controls Stage 0 guardrails for the tenant: 'block', 'inject', "
            "'off', or 'inherit'."
        ),
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="Optional operator notes (e.g. compliance profile name).",
    )
    is_active: bool = Field(
        default=True,
        description="Whether this policy restriction is currently enforced.",
    )


class TenantPrivacyPolicyResponse(BaseModel):
    """Serialized per-tenant privacy & cache policy."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    semantic_cache_enabled: bool
    zero_retention: bool
    pii_scrubbing_mode: PiiScrubbingPolicyMode
    guardrails_mode: GuardrailsPolicyMode
    description: str | None = None
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TenantPrivacyPolicyListResponse(BaseModel):
    """Paginated list of admin-configured tenant privacy policies."""

    policies: list[TenantPrivacyPolicyResponse]
    total: int
    skip: int
    limit: int


@dataclass(frozen=True)
class EffectiveTenantPolicy:
    """Thread-safe immutable snapshot used on the request hot path."""

    tenant_id: str
    semantic_cache_enabled: bool = True
    zero_retention: bool = False
    pii_scrubbing_mode: PiiScrubbingPolicyMode = "inherit"
    guardrails_mode: GuardrailsPolicyMode = "inherit"
    is_active: bool = True
