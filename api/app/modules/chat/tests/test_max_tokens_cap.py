"""Tests for the 2M max_tokens hard ceiling across request schemas."""

import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.core.config import DEFAULT_MAX_COMPLETION_TOKENS_CAP, config
from app.modules.anthropic.anthropic_schema import (
    AnthropicMessage,
    AnthropicMessagesRequest,
)
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatEstimateRequest,
    ChatMessage,
)
from app.modules.conversation.conversation_schema import (
    ChatCompletionRequestWithConversation,
)


class MaxTokensCapTests(unittest.TestCase):
    def test_default_cap_is_two_million(self):
        self.assertEqual(DEFAULT_MAX_COMPLETION_TOKENS_CAP, 2_000_000)
        self.assertEqual(config.MAX_COMPLETION_TOKENS_CAP, 2_000_000)

    def test_schemas_accept_values_up_to_two_million(self):
        chat_req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="hi")],
            max_tokens=2_000_000,
        )
        self.assertEqual(chat_req.max_tokens, 2_000_000)

        estimate_req = ChatEstimateRequest(
            messages=[ChatMessage(role="user", content="hi")],
            max_tokens=2_000_000,
        )
        self.assertEqual(estimate_req.max_tokens, 2_000_000)

        conv_req = ChatCompletionRequestWithConversation(
            service_name="openai",
            model="gpt-4o",
            message="hi",
            max_tokens=2_000_000,
        )
        self.assertEqual(conv_req.max_tokens, 2_000_000)

        anth_req = AnthropicMessagesRequest(
            model="claude-3-7-sonnet-20250219",
            messages=[AnthropicMessage(role="user", content="hi")],
            max_tokens=2_000_000,
        )
        self.assertEqual(anth_req.max_tokens, 2_000_000)

    def test_schemas_reject_above_two_million_and_non_positive(self):
        for bad_value in (0, -10, 2_000_001):
            with self.subTest(bad_value=bad_value):
                with self.assertRaises(ValidationError):
                    ChatCompletionRequest(
                        service_name="openai",
                        model="gpt-4o",
                        messages=[ChatMessage(role="user", content="hi")],
                        max_tokens=bad_value,
                    )
                with self.assertRaises(ValidationError):
                    ChatEstimateRequest(
                        messages=[ChatMessage(role="user", content="hi")],
                        max_tokens=bad_value,
                    )
                with self.assertRaises(ValidationError):
                    ChatCompletionRequestWithConversation(
                        service_name="openai",
                        model="gpt-4o",
                        message="hi",
                        max_tokens=bad_value,
                    )
                with self.assertRaises(ValidationError):
                    AnthropicMessagesRequest(
                        model="claude-3-7-sonnet-20250219",
                        messages=[AnthropicMessage(role="user", content="hi")],
                        max_tokens=bad_value,
                    )

    def test_runtime_config_cap_override_is_enforced(self):
        with patch.object(config, "MAX_COMPLETION_TOKENS_CAP", 32_768):
            ok = ChatCompletionRequest(
                service_name="openai",
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="hi")],
                max_tokens=32_768,
            )
            self.assertEqual(ok.max_tokens, 32_768)

            with self.assertRaises(ValidationError):
                ChatCompletionRequest(
                    service_name="openai",
                    model="gpt-4o",
                    messages=[ChatMessage(role="user", content="hi")],
                    max_tokens=32_769,
                )
            with self.assertRaises(ValidationError):
                ChatCompletionRequestWithConversation(
                    service_name="openai",
                    model="gpt-4o",
                    message="hi",
                    max_tokens=32_769,
                )
            with self.assertRaises(ValidationError):
                AnthropicMessagesRequest(
                    model="claude-3-7-sonnet-20250219",
                    messages=[AnthropicMessage(role="user", content="hi")],
                    max_tokens=32_769,
                )


if __name__ == "__main__":
    unittest.main()
