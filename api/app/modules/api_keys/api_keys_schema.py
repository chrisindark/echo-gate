from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    tenant_id: str = Field(..., min_length=1, max_length=100)
    user_id: str | None = Field(default=None, max_length=100)


class ApiKeyResponse(BaseModel):
    id: int
    name: str
    key_prefix: str
    tenant_id: str
    user_id: str | None = None
    is_active: bool
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApiKeyCreatedResponse(ApiKeyResponse):
    api_key: str


class ApiKeyRotateRequest(BaseModel):
    grace_hours: int = Field(
        default=24,
        ge=1,
        le=720,
        description="Grace period in hours before the old key expires",
    )


class ApiKeyRotateResponse(BaseModel):
    new_key: ApiKeyCreatedResponse
    expiring_key: ApiKeyResponse
