from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class LlmUsageLogBase(BaseModel):
    service_name: str
    model: str
    caller_service_name: str
    input_text: str | None = None
    output_text: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost: float = 0.0
    latency_ms: int | None = None
    status_code: int | None = None
    is_success: bool = True
    user_id: str | None = None
    tenant_id: str | None = None
    session_id: str | None = None
    error_message: str | None = None
    finish_reason: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    extra_metadata: dict | None = None


class LlmUsageLogCreate(LlmUsageLogBase):
    pass


class LlmUsageLogUpdate(BaseModel):
    input_text: str | None = None
    output_text: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost: Decimal | None = None
    latency_ms: int | None = None
    status_code: int | None = None
    is_success: bool | None = None
    error_message: str | None = None
    finish_reason: str | None = None
    cost_calculated: bool = False
    extra_metadata: dict | None = None


class LlmUsageLogResponse(LlmUsageLogBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
