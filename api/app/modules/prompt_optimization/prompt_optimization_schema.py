from __future__ import annotations

from pydantic import BaseModel, Field


class PromptOptimizationConfig(BaseModel):
    """Configuration options for prompt token optimization."""

    enabled: bool = Field(
        default=True,
        description="Whether prompt optimization is enabled for this request.",
    )
    whitespace: bool = Field(
        default=True,
        description="Collapse excessive horizontal spaces, tabs, and duplicate newline runs across prompt turns.",
    )
    session_dedup: bool = Field(
        default=True,
        description="Detect and deduplicate exact-match duplicate message turns in multi-turn sessions.",
    )
    json_table: bool = Field(
        default=True,
        description="Rewrite large uniform JSON arrays returned from tool calls into compact markdown pipe tables.",
    )
    json_table_min_rows: int = Field(
        default=2,
        ge=2,
        description="Minimum number of rows required to convert a tool JSON array into a markdown table.",
    )
    # Phase B: Context & Headroom Management
    ccr: bool = Field(
        default=False,
        description="Conversation context reduction: retain system prompt and last N turns while condensing intermediate turns.",
    )
    ccr_keep_last_n_turns: int = Field(
        default=5,
        ge=1,
        description="Number of recent conversational turns to preserve in full fidelity.",
    )
    ccr_strategy: str = Field(
        default="condense",
        description="Strategy for intermediate turns: 'condense' (summarize into compact briefing) or 'drop' (omit with notice).",
    )
    ccr_max_turn_chars: int = Field(
        default=150,
        ge=20,
        description="Maximum characters per turn snippet when condensing intermediate turns.",
    )
    headroom_trim: bool = Field(
        default=False,
        description="Dynamically prune oldest turns to guarantee prompt fit within the selected model's context window minus completion tokens.",
    )
    headroom_buffer_tokens: int = Field(
        default=100,
        ge=0,
        description="Safety buffer tokens below the target model context limit.",
    )
    headroom_target_tokens: int | None = Field(
        default=None,
        description="Explicit ceiling for maximum prompt tokens (overrides model context limit lookup).",
    )
    # Phase C: Lossy Prompt Compression (offloaded to inference-platform)
    compress: bool = Field(
        default=False,
        description="Lossy prompt compression via token pruning or extractive semantic ranking offloaded to inference-platform.",
    )
    compress_rate: float = Field(
        default=0.5,
        ge=0.05,
        le=0.95,
        description="Target compression ratio (fraction of content to retain).",
    )
    compress_target_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Target token count ceiling; overrides rate if specified.",
    )
    compress_method: str = Field(
        default="lexical",
        description="Compression method: 'lexical' (token pruning) or 'extractive' (sentence ranking).",
    )


class PromptOptimizationResult(BaseModel):
    """Metrics and metadata about the prompt optimization run."""

    applied: bool = Field(
        default=False,
        description="Whether any optimization was applied.",
    )
    original_tokens: int = Field(
        default=0,
        description="Estimated or counted prompt tokens before optimization.",
    )
    optimized_tokens: int = Field(
        default=0,
        description="Estimated or counted prompt tokens after optimization.",
    )
    tokens_saved: int = Field(
        default=0,
        description="Tokens saved by the optimization pipeline.",
    )
    original_chars: int = Field(
        default=0,
        description="Total character count before optimization.",
    )
    optimized_chars: int = Field(
        default=0,
        description="Total character count after optimization.",
    )
    chars_saved: int = Field(
        default=0,
        description="Characters saved by the optimization pipeline.",
    )
    strategies: list[str] = Field(
        default_factory=list,
        description="List of optimization strategies that modified the request.",
    )
