"""Request/response models for LLM quota rule CRUD."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LlmQuotaRuleBase(BaseModel):
    """Scope fields shared by create and update payloads.

    A ``None`` value is a wildcard, so a rule with all four scope fields unset
    applies globally. ``tenant_id`` left unset creates a global rule that every
    tenant sees; setting it scopes the rule to that single tenant.
    """

    provider: str | None = Field(default=None, max_length=50)
    model: str | None = Field(default=None, max_length=100)
    user_id: str | None = Field(default=None, max_length=100)
    tenant_id: str | None = Field(default=None, max_length=100)


class LlmQuotaRuleCreate(LlmQuotaRuleBase):
    max_rpm: int | None = Field(default=None, ge=1)
    max_tpm: int | None = Field(default=None, ge=1)
    is_active: bool = True

    @model_validator(mode="after")
    def _require_a_limit(self) -> LlmQuotaRuleCreate:
        # A rule with neither limit would never restrict anything and would sit
        # in the table looking meaningful, so reject it at the boundary.
        if self.max_rpm is None and self.max_tpm is None:
            raise ValueError("At least one of max_rpm or max_tpm must be set.")
        return self


class LlmQuotaRuleUpdate(BaseModel):
    """Partial update. Omitted fields are left untouched."""

    provider: str | None = Field(default=None, max_length=50)
    model: str | None = Field(default=None, max_length=100)
    user_id: str | None = Field(default=None, max_length=100)
    tenant_id: str | None = Field(default=None, max_length=100)
    max_rpm: int | None = Field(default=None, ge=1)
    max_tpm: int | None = Field(default=None, ge=1)
    is_active: bool | None = None


class LlmQuotaRuleResponse(BaseModel):
    id: int
    provider: str | None = None
    model: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    max_rpm: int | None = None
    max_tpm: int | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LlmQuotaRuleListResponse(BaseModel):
    rules: list[LlmQuotaRuleResponse]
    total: int
    skip: int
    limit: int
