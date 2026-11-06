"""Prompt Token Optimization Pipeline (Phase 5 / P3).

Provides opt-in pre-processing for chat completion requests to reduce token
footprint before semantic cache key generation and upstream LLM dispatch.
"""

from app.modules.prompt_optimization.prompt_optimization_schema import (
    PromptOptimizationConfig,
    PromptOptimizationResult,
)

__all__ = [
    "PromptOptimizationConfig",
    "PromptOptimizationResult",
    "PromptOptimizer",
]


def __getattr__(name: str):
    if name == "PromptOptimizer":
        from app.modules.prompt_optimization.prompt_optimizer import PromptOptimizer

        return PromptOptimizer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
