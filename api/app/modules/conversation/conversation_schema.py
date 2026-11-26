import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.config import DEFAULT_MAX_COMPLETION_TOKENS_CAP
from app.core.helpers import validate_max_tokens_cap
from app.modules.prompt_optimization.prompt_optimization_schema import (
    PromptOptimizationConfig,
)


class MessageCreate(BaseModel):
    role: str
    content: str


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class ConversationCreate(BaseModel):
    title: str | None = "New Conversation"


class ConversationUpdate(BaseModel):
    title: str


class ConversationDeleteResponse(BaseModel):
    id: str
    deleted: bool = True


class ConversationResponse(BaseModel):
    id: str
    user_id: str
    title: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}


class ConversationWithMessagesResponse(ConversationResponse):
    messages: list[MessageResponse]


class ChatCompletionRequestWithConversation(BaseModel):
    service_name: str | None = "ollama"
    model: str
    message: str
    temperature: float | None = 1.0
    max_tokens: int | None = Field(
        default=None, ge=1, le=DEFAULT_MAX_COMPLETION_TOKENS_CAP
    )
    stream: bool = False

    @field_validator("max_tokens")
    @classmethod
    def _validate_max_tokens(cls, value: int | None) -> int | None:
        return validate_max_tokens_cap(value)

    thinking: bool = False
    thinking_budget: int | None = None
    response_format: dict[str, Any] | None = None
    prompt_optimization: PromptOptimizationConfig | bool | None = None
