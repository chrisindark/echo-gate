"""Tests for the pre-flight cost estimate endpoint (Phase 3, step 3).

Covers the tokenizer helper (BPE path, offline fallback), chat framing overhead,
and the estimate matrix: enabled models only, pricing from `core/pricing.py`,
cheapest-first ordering, and `max_tokens` handling.
"""

import unittest
from unittest.mock import patch

from app.core import helpers
from app.core.config import config
from app.core.helpers import count_tokens, reset_tokenizer_cache, tokenizer_label
from app.modules.chat.chat_controller import estimate_chat_cost
from app.modules.chat.chat_estimate_service import (
    MESSAGE_OVERHEAD_TOKENS,
    REPLY_PRIMING_TOKENS,
    ChatEstimateService,
    count_message_tokens,
    count_tool_tokens,
)
from app.modules.chat.chat_schema import (
    ChatEstimateRequest,
    ChatMessage,
    FunctionCall,
    FunctionDefinition,
    Tool,
    ToolCall,
)
from app.modules.llm.model_registry import ModelTarget


class _FakeEncoder:
    def __init__(self, tokens_per_char: int = 1):
        self.calls: list[str] = []
        self.tokens_per_char = tokens_per_char

    def encode(self, text, disallowed_special=()):
        self.calls.append(text)
        return list(range(max(1, len(text) * self.tokens_per_char)))


class TokenCounterTests(unittest.TestCase):
    def setUp(self):
        reset_tokenizer_cache()

    def tearDown(self):
        reset_tokenizer_cache()

    def test_uses_the_bpe_encoding_when_available(self):
        encoder = _FakeEncoder()
        with patch.object(helpers, "get_tokenizer", return_value=encoder):
            self.assertEqual(count_tokens("hello", "gpt-4o-mini"), 5)
        self.assertEqual(encoder.calls, ["hello"])

    def test_offline_engine_falls_back_to_a_length_estimate(self):
        with patch.object(helpers, "get_tokenizer", return_value=None):
            self.assertEqual(count_tokens("abcdefgh", "gpt-4o-mini"), 2)
            self.assertEqual(count_tokens("", "gpt-4o-mini"), 0)
            self.assertIn("heuristic", tokenizer_label("gpt-4o-mini"))

    def test_encoder_failure_falls_back_instead_of_raising(self):
        class _Broken:
            def encode(self, text, disallowed_special=()):
                raise ValueError("bad text")

        with patch.object(helpers, "get_tokenizer", return_value=_Broken()):
            self.assertEqual(count_tokens("abcdefgh", "gpt-4o-mini"), 2)

    def test_model_family_selects_the_vocabulary(self):
        self.assertEqual(helpers.tokenizer_name_for_model("gpt-4o-mini"), "o200k_base")
        self.assertEqual(
            helpers.tokenizer_name_for_model("claude-3-7-sonnet-20250219"),
            "cl100k_base",
        )


class MessageTokenTests(unittest.TestCase):
    def setUp(self):
        # One "token" per whitespace-separated word keeps the framing math exact.
        self._patch = patch(
            "app.modules.chat.chat_estimate_service.count_tokens",
            side_effect=lambda text, model: len(text.split()),
        )
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_message_overhead_and_reply_priming_are_counted(self):
        tokens = count_message_tokens(
            [ChatMessage(role="user", content="hello world")], "gpt-4o-mini"
        )
        self.assertEqual(
            tokens,
            REPLY_PRIMING_TOKENS + MESSAGE_OVERHEAD_TOKENS + 1 + 2,
        )

    def test_tool_calls_and_tool_outputs_are_counted(self):
        message = ChatMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    function=FunctionCall(
                        name="get_weather", arguments='{"city": "Paris"}'
                    ),
                )
            ],
        )
        plain = count_message_tokens([ChatMessage(role="assistant")], "gpt-4o-mini")
        with_tool_call = count_message_tokens([message], "gpt-4o-mini")
        self.assertGreater(with_tool_call, plain)

        tool_result = ChatMessage(
            role="tool", content="18 degrees", tool_call_id="call_1"
        )
        self.assertGreater(
            count_message_tokens([tool_result], "gpt-4o-mini"),
            count_message_tokens(
                [ChatMessage(role="tool", content="18 degrees")], "gpt-4o-mini"
            ),
        )

    def test_tool_declarations_add_tokens_only_when_present(self):
        tools = [
            Tool(
                function=FunctionDefinition(
                    name="get_weather",
                    description="Look up the weather for a city",
                    parameters={
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                )
            )
        ]
        self.assertEqual(count_tool_tokens(None, "gpt-4o-mini"), 0)
        self.assertGreater(count_tool_tokens(tools, "gpt-4o-mini"), 0)


class _Registry:
    def __init__(self, models, disabled=(), aliases=None):
        self.models = models
        self.disabled = set(disabled)
        self.aliases = aliases or {}

    def get_allowed_models_dict(self):
        return self.models

    def is_model_enabled(self, service, model):
        return (service, model) not in self.disabled

    def is_alias(self, name):
        return name in self.aliases

    def get_alias_targets(self, alias):
        return self.aliases[alias]


def _service(registry) -> ChatEstimateService:
    service = ChatEstimateService()
    service.registry = registry
    return service


class EstimateTests(unittest.TestCase):
    def setUp(self):
        self._token_patch = patch(
            "app.modules.chat.chat_estimate_service.count_tokens",
            return_value=100,
        )
        self._token_patch.start()
        self.addCleanup(self._token_patch.stop)
        self._completion_patch = patch.object(
            config, "ESTIMATE_DEFAULT_COMPLETION_TOKENS", 256
        )
        self._completion_patch.start()
        self.addCleanup(self._completion_patch.stop)
        # No network in unit tests: report the vocabulary name without loading
        # the BPE file (TokenCounterTests covers the encoder itself).
        self._label_patch = patch(
            "app.modules.chat.chat_estimate_service.tokenizer_label",
            side_effect=helpers.tokenizer_name_for_model,
        )
        self._label_patch.start()
        self.addCleanup(self._label_patch.stop)

    def _request(self, **overrides) -> ChatEstimateRequest:
        payload = {
            "messages": [ChatMessage(role="user", content="Explain prefix caching")],
        }
        payload.update(overrides)
        return ChatEstimateRequest(**payload)

    def test_matrix_covers_enabled_models_only(self):
        registry = _Registry(
            {
                "openai": ["gpt-4o-mini"],
                "claude": ["claude-3-7-sonnet-20250219"],
                "ollama": ["qwen3:8b"],
            },
            disabled={("ollama", "qwen3:8b")},
        )
        response = _service(registry).estimate(self._request())

        pairs = {(estimate.service, estimate.model) for estimate in response.estimates}
        self.assertEqual(
            pairs,
            {
                ("openai", "gpt-4o-mini"),
                ("claude", "claude-3-7-sonnet-20250219"),
            },
        )

    def test_costs_follow_the_pricing_table(self):
        registry = _Registry({"openai": ["gpt-4o-mini"]})
        with patch(
            "app.modules.chat.chat_estimate_service.count_tokens",
            side_effect=lambda text, model: 100 if model else 0,
        ):
            response = _service(registry).estimate(self._request(max_tokens=50))
        # count_tokens returns 100 for every fragment: 3 priming + 4 message
        # overhead + role + content.
        self.assertEqual(response.prompt_tokens, 207)
        self.assertEqual(response.completion_tokens, 50)

        estimate = response.estimates[0]
        self.assertAlmostEqual(estimate.cost_prompt, 207 * 0.150 / 1_000_000, places=8)
        self.assertAlmostEqual(
            estimate.cost_completion, 50 * 0.600 / 1_000_000, places=8
        )
        self.assertAlmostEqual(
            estimate.cost_total,
            estimate.cost_prompt + estimate.cost_completion,
            places=8,
        )

    def test_estimates_are_sorted_cheapest_first(self):
        registry = _Registry(
            {
                "claude": ["claude-3-7-sonnet-20250219"],
                "openai": ["gpt-4o-mini"],
                "ollama": ["qwen3:8b"],
            }
        )
        response = _service(registry).estimate(self._request())
        totals = [estimate.cost_total for estimate in response.estimates]
        self.assertEqual(totals, sorted(totals))
        self.assertEqual(response.estimates[0].service, "ollama")
        self.assertEqual(response.estimates[-1].service, "claude")

    def test_provider_spelling_does_not_change_the_price(self):
        registry = _Registry(
            {
                "gemini": ["gemini-3.5-flash-lite"],
                "google-genai": ["gemini-3.5-flash-lite"],
            }
        )
        response = _service(registry).estimate(self._request())
        costs = {
            estimate.service: estimate.cost_total for estimate in response.estimates
        }
        self.assertGreater(costs["google-genai"], 0)
        self.assertEqual(costs["gemini"], costs["google-genai"])

    def test_completion_tokens_default_to_the_configured_budget(self):
        registry = _Registry({"openai": ["gpt-4o-mini"]})
        response = _service(registry).estimate(self._request())
        self.assertEqual(response.completion_tokens, 256)

    def test_aliases_resolve_to_their_first_target_for_the_tokenizer(self):
        registry = _Registry(
            {"openai": ["gpt-4o-mini"]},
            aliases={"cheap": [ModelTarget(service="openai", model="gpt-4o-mini")]},
        )
        response = _service(registry).estimate(self._request(model="cheap"))
        self.assertEqual(response.model, "gpt-4o-mini")
        self.assertEqual(response.tokenizer, "o200k_base")

    def test_tools_are_included_in_the_prompt_count(self):
        registry = _Registry({"openai": ["gpt-4o-mini"]})
        tool = Tool(
            function=FunctionDefinition(
                name="get_weather",
                description="Look up the weather",
                parameters={"type": "object"},
            )
        )
        without_tools = _service(registry).estimate(self._request())
        with_tools = _service(registry).estimate(self._request(tools=[tool]))
        self.assertGreater(with_tools.prompt_tokens, without_tools.prompt_tokens)


class EstimateControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._label_patch = patch(
            "app.modules.chat.chat_estimate_service.tokenizer_label",
            side_effect=helpers.tokenizer_name_for_model,
        )
        self._label_patch.start()
        self.addCleanup(self._label_patch.stop)

    async def test_controller_returns_the_estimate(self):
        response = await estimate_chat_cost(
            ChatEstimateRequest(messages=[ChatMessage(role="user", content="hi")])
        )
        self.assertGreater(response.prompt_tokens, 0)
        self.assertTrue(response.estimates)
        self.assertTrue(all(e.cost_total >= 0 for e in response.estimates))


if __name__ == "__main__":
    unittest.main()
