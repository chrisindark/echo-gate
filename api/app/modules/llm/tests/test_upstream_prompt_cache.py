"""Upstream (provider-native) prompt-cache accounting and passthrough.

These cover the P4 item 5 slice: Anthropic ``cache_control`` survives the
translation into the internal request, provider-reported cache token counts are
normalized onto ``Usage`` and the reverse Anthropic envelope, and cost pricing
applies each provider's cache multipliers.
"""

import pytest

from app.core.pricing import calculate_cost, get_cache_token_multipliers
from app.modules.anthropic.anthropic_adapter import (
    normalize_anthropic_to_chat_request,
    normalize_chat_to_anthropic_response,
)
from app.modules.anthropic.anthropic_schema import AnthropicMessagesRequest
from app.modules.chat.chat_schema import (
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    PromptTokensDetails,
    Usage,
)
from app.modules.llm.llm_provider_service import LlmProviderService

CLAUDE_MODEL = "claude-3-5-sonnet-20241022"


# --- Usage normalization -----------------------------------------------------


def test_openai_cached_tokens_are_reported_as_cache_reads():
    usage = Usage(
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        prompt_tokens_details=PromptTokensDetails(cached_tokens=40),
    )
    assert usage.cache_read_tokens == 40
    assert usage.cache_creation_tokens == 0


def test_anthropic_cache_fields_are_reported_separately():
    usage = Usage(
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        cache_read_input_tokens=30,
        cache_creation_input_tokens=20,
    )
    assert usage.cache_read_tokens == 30
    assert usage.cache_creation_tokens == 20


def test_usage_without_provider_cache_reports_zero():
    usage = Usage(prompt_tokens=10, completion_tokens=2, total_tokens=12)
    assert usage.cache_read_tokens == 0
    assert usage.cache_creation_tokens == 0


# --- Pricing ----------------------------------------------------------------


def test_provider_cache_multipliers():
    assert get_cache_token_multipliers("openai") == (0.5, 1.0)
    assert get_cache_token_multipliers("claude") == (0.1, 1.25)
    assert get_cache_token_multipliers("anthropic") == (0.1, 1.25)
    assert get_cache_token_multipliers("gemini") == (0.25, 1.0)
    assert get_cache_token_multipliers("unknown") == (1.0, 1.0)


def test_calculate_cost_discounts_openai_cache_reads():
    # gpt-4o-mini prompt price is 0.15 / 1M tokens; cache reads are 0.5x.
    cost = calculate_cost(
        "openai",
        "gpt-4o-mini",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cache_read_tokens=1_000_000,
    )
    assert cost == pytest.approx(0.075)


def test_calculate_cost_prices_anthropic_reads_and_writes():
    # claude-3-5-sonnet input price is 3.0 / 1M; reads 0.1x, writes 1.25x.
    cost = calculate_cost(
        "claude",
        CLAUDE_MODEL,
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cache_read_tokens=500_000,
        cache_creation_tokens=500_000,
    )
    expected = 0.5 * 3.0 * 0.1 + 0.5 * 3.0 * 1.25
    assert cost == pytest.approx(expected)


def test_calculate_cost_never_goes_negative_when_counts_exceed_prompt():
    cost = calculate_cost(
        "openai",
        "gpt-4o-mini",
        prompt_tokens=10,
        completion_tokens=0,
        cache_read_tokens=100,
    )
    assert cost >= 0.0


# --- Anthropic cache_control preservation -----------------------------------


def test_anthropic_cache_control_survives_normalization():
    request = AnthropicMessagesRequest(
        model=CLAUDE_MODEL,
        max_tokens=16,
        system=[
            {
                "type": "text",
                "text": "You are a helpful assistant.",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Hi",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        ],
    )

    chat_request = normalize_anthropic_to_chat_request(request)

    assert chat_request.messages[0].role == "system"
    assert chat_request.messages[0].cache_control == {"type": "ephemeral"}
    assert chat_request.messages[1].role == "user"
    assert chat_request.messages[1].cache_control == {"type": "ephemeral"}


def test_claude_payload_reattaches_cache_control_blocks():
    provider = LlmProviderService()
    provider.claude_base_url = "https://api.anthropic.com"
    provider.claude_api_key = "test-key"

    system_message = ChatMessage(role="system", content="System prompt")
    system_message.cache_control = {"type": "ephemeral"}
    user_message = ChatMessage(role="user", content="Hello")
    user_message.cache_control = {"type": "ephemeral"}
    request = AnthropicMessagesRequest(
        model=CLAUDE_MODEL,
        max_tokens=16,
        messages=[{"role": "user", "content": "placeholder"}],
    )
    chat_request = normalize_anthropic_to_chat_request(request)
    chat_request.messages = [system_message, user_message]

    _, _, payload = provider._prepare_claude_payload(chat_request, stream=False)

    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert payload["messages"][0]["content"][0]["cache_control"] == {
        "type": "ephemeral"
    }


# --- Reverse Anthropic usage ------------------------------------------------


def test_anthropic_response_reports_cache_tokens_separately():
    response = ChatCompletionResponse(
        id="chatcmpl-1",
        created=1,
        model=CLAUDE_MODEL,
        choices=[
            Choice(
                index=0,
                message=ChatMessage(role="assistant", content="Hello"),
                finish_reason="stop",
            )
        ],
        usage=Usage(
            prompt_tokens=100,
            completion_tokens=10,
            total_tokens=110,
            cache_read_input_tokens=30,
            cache_creation_input_tokens=20,
        ),
    )

    anthropic_response = normalize_chat_to_anthropic_response(response)

    assert anthropic_response.usage.input_tokens == 50
    assert anthropic_response.usage.output_tokens == 10
    assert anthropic_response.usage.cache_read_input_tokens == 30
    assert anthropic_response.usage.cache_creation_input_tokens == 20
