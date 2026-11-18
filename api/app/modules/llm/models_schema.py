"""Response models for model discovery.

``GET /v1/models`` is shared by the OpenAI and Anthropic SDKs, which expect
different envelopes on the same path. The OpenAI shape is the gateway's native
one; the Anthropic shape is returned when the caller sends
``anthropic-version`` (the Anthropic SDK always does).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ModelTargetInfo(BaseModel):
    """One ranked provider target behind an alias."""

    service: str
    model: str
    available: bool
    enabled: bool


class ModelInfo(BaseModel):
    """An OpenAI-shaped model card with Echo Gate routing metadata.

    ``id``/``object``/``created``/``owned_by`` are the OpenAI SDK's required
    fields; everything else is additive metadata that the SDK tolerates.
    """

    id: str
    object: Literal["model"] = "model"
    created: int = Field(
        default=0,
        description=(
            "Unix timestamp. The model registry carries no release dates, so "
            "this is 0 ('unknown') rather than an invented date."
        ),
    )
    owned_by: str = Field(
        description=(
            "Provider service that serves the model, or 'echo-gate' for a "
            "routing alias."
        )
    )
    available: bool = Field(
        description=(
            "True when at least one hosting provider is configured. Configured "
            "is not the same as healthy: a provider can still fail at call time."
        )
    )
    enabled: bool = Field(
        description=(
            "False when the registry marks the model disabled. Aliases have no "
            "disable flag and are always enabled."
        )
    )
    providers: list[str] = Field(
        description="Provider services hosting the model, or an alias's targets."
    )
    context_window: int = Field(description="Maximum context window in tokens.")
    targets: list[ModelTargetInfo] | None = Field(
        default=None,
        description="Ranked targets for an alias; null for a concrete model.",
    )


class ModelListResponse(BaseModel):
    """OpenAI-compatible `GET /v1/models` envelope."""

    object: Literal["list"] = "list"
    data: list[ModelInfo]


class AnthropicModelInfo(BaseModel):
    """The model shape the Anthropic Models API returns."""

    type: Literal["model"] = "model"
    id: str
    display_name: str
    created_at: str


class AnthropicModelListResponse(BaseModel):
    """Anthropic-compatible `GET /v1/models` envelope."""

    data: list[AnthropicModelInfo]
    has_more: bool = False
    first_id: str | None = None
    last_id: str | None = None
