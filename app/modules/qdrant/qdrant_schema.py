from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CacheScope(str, Enum):
    GLOBAL = "GLOBAL"
    TENANT = "TENANT"
    USER = "USER"
    SESSION = "SESSION"
    CONVERSATION = "CONVERSATION"


from app.modules.chat.chat_schema import ChatMessage

class QdrantSearchRequest(BaseModel):
    messages: list[ChatMessage]
    threshold: float = 0.90
    model_filter: str | None = None


class QdrantSearchResponse(BaseModel):
    found: bool
    payload: list[dict[str, Any]] | None = None


class QdrantPayload(BaseModel):
    """
    Schema for saving vectors to Qdrant.
    """

    prompt: str = Field(..., description="The original prompt string that was embedded")
    system_prompt: str | None = Field(None, description="The system part of the prompt")
    user_prompt: str | None = Field(None, description="The user part of the prompt")
    response: dict[str, Any] = Field(
        ..., description="The LLM completion response dictionary"
    )
    exact_hash: str | None = Field(
        None, description="SHA256 hash of the exact prompt for fast exact matching"
    )
    tenant_id: str | None = Field(
        None, description="The tenant ID associated with this cache entry"
    )
    user_id: str | None = Field(
        None, description="The user ID associated with this cache entry"
    )
    session_id: str | None = Field(
        None, description="The session ID associated with this cache entry"
    )
    conversation_id: str | None = Field(
        None, description="The conversation ID associated with this cache entry"
    )
    model: str | None = Field(None, description="The model used for the completion")
    service_name: str | None = Field(None, description="The LLM provider service name")
    scope: str = Field(
        "GLOBAL", description="Cache scope: GLOBAL, TENANT, USER, SESSION, CONVERSATION"
    )
    entities: list[str] = Field(
        default_factory=list, description="List of extracted entities"
    )
    time_sensitivity: float | None = Field(
        None, description="Score of how time sensitive this query is"
    )
    embedding_model: str | None = Field(
        None, description="The model used for embeddings"
    )
    embedding_version: str | None = Field(
        None, description="The version of the model used for embeddings"
    )
    cacheable: bool = Field(
        False, description="Value to denote if the result is to be cached"
    )
    cache_key_version: str | None = Field(
        None,
        description="The version of the cache to be queried if embeddings are changed in the future",
    )
    created_at: int | None = Field(
        None, description="Unix timestamp of when this cache entry was created"
    )
    expires_at: int | None = Field(
        None, description="Unix timestamp of when this cache entry expires"
    )
    metadata: dict[str, Any] | None = Field(
        default_factory=dict, description="Additional custom metadata"
    )

    def to_qdrant_dict(self) -> dict[str, Any]:
        """Convert the schema to a dictionary for Qdrant payload."""
        base = self.model_dump(exclude_none=True, exclude={"metadata"})
        if self.metadata:
            base.update(self.metadata)
        return base
