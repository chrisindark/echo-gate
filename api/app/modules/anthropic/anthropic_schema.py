"""Pydantic schemas for the Anthropic Messages API specification.

Matches Anthropic Messages API wire format:
https://docs.anthropic.com/en/api/messages
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.prompt_optimization.prompt_optimization_schema import (
    PromptOptimizationConfig,
)


class AnthropicTextBlock(BaseModel):
    """Text content block in an Anthropic message."""

    type: Literal["text"] = "text"
    text: str


class AnthropicImageSource(BaseModel):
    """Base64 source payload for an image block."""

    type: Literal["base64"] = "base64"
    media_type: str
    data: str


class AnthropicImageBlock(BaseModel):
    """Image content block in an Anthropic message."""

    type: Literal["image"] = "image"
    source: AnthropicImageSource


class AnthropicToolUseBlock(BaseModel):
    """Tool invocation block emitted by the assistant."""

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class AnthropicToolResultBlock(BaseModel):
    """Result of a tool execution returned by the user."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | list[dict[str, Any]] | None = None
    is_error: bool = False


class AnthropicMessage(BaseModel):
    """A conversational turn in an Anthropic Messages request."""

    role: Literal["user", "assistant"]
    content: str | list[dict[str, Any]]


class AnthropicTool(BaseModel):
    """Tool specification in Anthropic format."""

    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


class AnthropicToolChoice(BaseModel):
    """Tool choice forcing or automated policy."""

    type: Literal["auto", "any", "tool"] = "auto"
    name: str | None = None


class AnthropicMessagesRequest(BaseModel):
    """Request payload for POST /api/v1/messages."""

    model: str
    messages: list[AnthropicMessage]
    max_tokens: int = Field(default=1024, ge=1)
    system: str | list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    stop_sequences: list[str] | None = None
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=1)
    tools: list[AnthropicTool] | None = None
    tool_choice: AnthropicToolChoice | None = None

    # Gateway passthrough parameters
    prompt_optimization: PromptOptimizationConfig | bool | None = None
    cache_enabled: bool | None = None
    cache_ttl: int | None = Field(default=None, ge=0)
    retries: int | None = Field(default=None, ge=0, le=10)
    stream_pacing: bool | None = None
    stream_pacing_delay_ms: float | None = Field(default=None, ge=0.0, le=1000.0)
    service_name: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    conversation_id: str | None = None


class AnthropicUsage(BaseModel):
    """Token usage metrics reported in an Anthropic message response."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None


class AnthropicMessagesResponse(BaseModel):
    """Response payload for POST /api/v1/messages."""

    id: str
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    model: str
    content: list[dict[str, Any]]
    stop_reason: (
        Literal["end_turn", "max_tokens", "stop_sequence", "tool_use"] | None
    ) = "end_turn"
    stop_sequence: str | None = None
    usage: AnthropicUsage


def format_anthropic_sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format an SSE frame conforming to the Anthropic streaming specification."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
