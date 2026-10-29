"""Pre-flight response cost estimation (Phase 3, step 3).

Answers "what will this prompt cost on each enabled model?" without calling any
provider and without touching the cache. Prompt tokens come from a BPE count of
the serialized chat request; completion tokens are unknowable before
generation, so the estimate assumes `max_tokens` when the caller sets it and
`ESTIMATE_DEFAULT_COMPLETION_TOKENS` otherwise.
"""

import json
import logging

from app.core.config import config
from app.core.helpers import count_tokens, tokenizer_label
from app.core.pricing import get_model_prices
from app.modules.chat.chat_schema import (
    ChatEstimateRequest,
    ChatEstimateResponse,
    ChatMessage,
    ModelCostEstimate,
    Tool,
)
from app.modules.llm.model_registry import model_registry

logger = logging.getLogger(__name__)

# OpenAI's chat format adds a fixed overhead per message plus reply priming.
# Without it a one-word greeting counts as a single token and every estimate
# lands low.
MESSAGE_OVERHEAD_TOKENS = 4
REPLY_PRIMING_TOKENS = 3
NAME_OVERHEAD_TOKENS = 1
DEFAULT_TOKENIZER_MODEL = "gpt-4o"


def count_message_tokens(messages: list[ChatMessage], model: str) -> int:
    """Prompt tokens for the message array, including chat framing overhead."""
    total = REPLY_PRIMING_TOKENS
    for message in messages:
        total += MESSAGE_OVERHEAD_TOKENS
        total += count_tokens(message.role or "", model)
        if message.name:
            total += count_tokens(message.name, model) + NAME_OVERHEAD_TOKENS
        if message.content:
            total += count_tokens(message.content, model)
        if message.tool_calls:
            serialized = json.dumps(
                [tool_call.model_dump() for tool_call in message.tool_calls],
                sort_keys=True,
            )
            total += count_tokens(serialized, model)
        if message.tool_call_id:
            total += count_tokens(message.tool_call_id, model)
    return total


def count_tool_tokens(tools: list[Tool] | None, model: str) -> int:
    """Token cost of the JSON tool declarations, which are sent with the prompt."""
    if not tools:
        return 0
    serialized = json.dumps(
        [tool.model_dump(exclude_none=True) for tool in tools],
        sort_keys=True,
    )
    return count_tokens(serialized, model)


class ChatEstimateService:
    """Estimate prompt tokens and per-model dollar cost for a chat request."""

    def __init__(self) -> None:
        self.registry = model_registry

    def estimate(self, request: ChatEstimateRequest) -> ChatEstimateResponse:
        model = self._tokenizer_model(request.model)
        prompt_tokens = count_message_tokens(
            request.messages, model
        ) + count_tool_tokens(request.tools, model)
        completion_tokens = (
            request.max_tokens
            if request.max_tokens
            else config.ESTIMATE_DEFAULT_COMPLETION_TOKENS
        )

        estimates = [
            self._estimate_for(service, candidate, prompt_tokens, completion_tokens)
            for service, models in self.registry.get_allowed_models_dict().items()
            for candidate in models
            if self.registry.is_model_enabled(service, candidate)
        ]
        # Cheapest first: the point of the endpoint is choosing a model, and
        # the provider/model tiebreak keeps the order stable across requests.
        estimates.sort(
            key=lambda estimate: (estimate.cost_total, estimate.service, estimate.model)
        )

        logger.debug(
            "Estimated %s prompt + %s completion tokens across %s models",
            prompt_tokens,
            completion_tokens,
            len(estimates),
        )
        return ChatEstimateResponse(
            model=model,
            tokenizer=tokenizer_label(model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimates=estimates,
        )

    def _tokenizer_model(self, requested: str | None) -> str:
        """Resolve an alias to a real model id; fall back to a default vocabulary."""
        hint = (requested or "").strip()
        if not hint:
            return DEFAULT_TOKENIZER_MODEL
        if self.registry.is_alias(hint):
            targets = self.registry.get_alias_targets(hint)
            if targets:
                return targets[0].model
        return hint

    @staticmethod
    def _estimate_for(
        service: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> ModelCostEstimate:
        prompt_price, completion_price = get_model_prices(service, model)
        cost_prompt = round(prompt_tokens * prompt_price, 8)
        cost_completion = round(completion_tokens * completion_price, 8)
        return ModelCostEstimate(
            service=service,
            model=model,
            cost_prompt=cost_prompt,
            cost_completion=cost_completion,
            cost_total=round(cost_prompt + cost_completion, 8),
        )


chat_estimate_service = ChatEstimateService()
