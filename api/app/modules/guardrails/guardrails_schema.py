"""Schemas and config models for Soft-Steering Guardrails."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

GuardrailMode = Literal["off", "block", "inject"]
GuardrailSeverity = Literal["low", "medium", "high", "critical"]


class GuardrailRule(BaseModel):
    """Single policy rule evaluated at Stage 0."""

    id: str = Field(min_length=1, max_length=100)
    action: Literal["block", "inject"] | None = None
    patterns: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    instruction: str | None = None
    message: str | None = None
    severity: GuardrailSeverity = "medium"


class GuardrailsConfig(BaseModel):
    """Per-request guardrails override."""

    enabled: bool = True
    mode: GuardrailMode | None = None
    instruction: str | None = None
    rules: list[GuardrailRule] = Field(default_factory=list)
    use_builtin_rules: bool = True


@dataclass
class GuardrailEvaluationResult:
    """Outcome of Stage 0 guardrail evaluation."""

    applied: bool = False
    mode: str = "off"
    action: Literal["none", "blocked", "injected"] = "none"
    matched_rule_ids: list[str] = field(default_factory=list)
    injected_instructions: list[str] = field(default_factory=list)
    block_reason: str | None = None
    severity: str | None = None

    @property
    def blocked(self) -> bool:
        return self.action == "blocked"

    @property
    def injected(self) -> bool:
        return self.action == "injected"

    def to_header_value(self) -> str:
        if not self.applied or self.action == "none":
            return ""
        if self.action == "blocked":
            rules = ",".join(self.matched_rule_ids) or "policy"
            return f"blocked:{rules}"
        return f"injected:{len(self.injected_instructions)}"
