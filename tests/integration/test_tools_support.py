import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatMessage,
    FunctionCall,
    FunctionDefinition,
    Tool,
    ToolCall,
)
from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.llm.llm_router_service import LlmRouterService


class TestToolsSupport(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sample_tool = Tool(
            type="function",
            function=FunctionDefinition(
                name="get_current_weather",
                description="Get current weather for location",
                parameters={
                    "type": "object",
                    "properties": {
                        "location": {"type": "string"},
                        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                    },
                    "required": ["location"],
                },
            ),
        )

    def test_schema_with_tools_and_tool_calls(self):
        req = ChatCompletionRequest(
            service_name="ollama",
            model="qwen3:8b",
            messages=[
                ChatMessage(role="user", content="What is the weather in NYC?"),
                ChatMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="call_abc123",
                            type="function",
                            function=FunctionCall(
                                name="get_current_weather",
                                arguments='{"location": "NYC"}',
                            ),
                        )
                    ],
                ),
                ChatMessage(
                    role="tool",
                    tool_call_id="call_abc123",
                    name="get_current_weather",
                    content='{"temperature": 72, "unit": "fahrenheit"}',
                ),
            ],
            tools=[self.sample_tool],
            tool_choice="auto",
        )
        self.assertEqual(len(req.messages), 3)
        self.assertIsNone(req.messages[1].content)
        self.assertEqual(
            req.messages[1].tool_calls[0].function.name, "get_current_weather"
        )
        self.assertEqual(req.messages[2].role, "tool")
        self.assertEqual(req.tools[0].function.name, "get_current_weather")
        self.assertEqual(req.tool_choice, "auto")

    def _create_router_service(self):
        return LlmRouterService(
            llm_provider_service=MagicMock(),
            embedding_service=MagicMock(),
            llm_cache_service=MagicMock(),
            redis_service=MagicMock(),
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=MagicMock(),
        )

    def test_cache_key_generation_uniqueness(self):
        router_svc = self._create_router_service()

        req1 = ChatCompletionRequest(
            service_name="ollama",
            model="qwen3:8b",
            messages=[ChatMessage(role="user", content="Check weather")],
        )
        req2 = ChatCompletionRequest(
            service_name="ollama",
            model="qwen3:8b",
            messages=[ChatMessage(role="user", content="Check weather")],
            tools=[self.sample_tool],
        )
        req3 = ChatCompletionRequest(
            service_name="ollama",
            model="qwen3:8b",
            messages=[ChatMessage(role="user", content="Check weather")],
            tools=[self.sample_tool],
            tool_choice="required",
        )

        key1 = router_svc._generate_cache_key(req1)
        key2 = router_svc._generate_cache_key(req2)
        key3 = router_svc._generate_cache_key(req3)

        self.assertNotEqual(key1, key2)
        self.assertNotEqual(key2, key3)
        # Identical request produces identical key
        self.assertEqual(key2, router_svc._generate_cache_key(req2))

    def test_prepare_prompts_with_tool_calls_and_tool_responses(self):
        router_svc = self._create_router_service()

        req = ChatCompletionRequest(
            service_name="ollama",
            model="qwen3:8b",
            messages=[
                ChatMessage(role="system", content="You are a helpful assistant."),
                ChatMessage(role="user", content="Weather?"),
                ChatMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            type="function",
                            function=FunctionCall(
                                name="get_current_weather",
                                arguments='{"location": "Boston"}',
                            ),
                        )
                    ],
                ),
                ChatMessage(
                    role="tool",
                    tool_call_id="call_1",
                    content='{"temp": "65F"}',
                ),
            ],
        )

        prompt_text, system_prompt, user_prompt = router_svc._prepare_prompts(req)
        self.assertEqual(system_prompt, "You are a helpful assistant.")
        self.assertEqual(user_prompt, "Weather?")
        self.assertIn(
            'assistant: [tool_calls: get_current_weather({"location": "Boston"})]',
            prompt_text,
        )
        self.assertIn('tool: {"temp": "65F"} [call_id: call_1]', prompt_text)

    async def test_semantic_cache_bypass_when_tools_present(self):
        router_svc = self._create_router_service()

        req = ChatCompletionRequest(
            service_name="ollama",
            model="qwen3:8b",
            messages=[ChatMessage(role="user", content="Weather?")],
            tools=[self.sample_tool],
        )

        result = await router_svc.evaluate_semantic_cache(req)
        self.assertIsNone(result.get("response"))
        self.assertEqual(result.get("matches"), [])

    def test_mock_response_with_tools(self):
        provider_svc = LlmProviderService()

        req_with_tools = ChatCompletionRequest(
            service_name="ollama",
            model="qwen3:8b",
            messages=[ChatMessage(role="user", content="Weather?")],
            tools=[self.sample_tool],
            tool_choice="required",
        )
        mock_resp = provider_svc.generate_mock_response(req_with_tools)
        self.assertEqual(mock_resp.choices[0].finish_reason, "tool_calls")
        self.assertIsNotNone(mock_resp.choices[0].message.tool_calls)
        self.assertEqual(
            mock_resp.choices[0].message.tool_calls[0].function.name,
            "get_current_weather",
        )

    async def test_gemini_translation(self):
        provider_svc = LlmProviderService()
        mock_gemini_client = MagicMock()
        mock_aio_models = MagicMock()
        mock_gemini_client.aio.models = mock_aio_models

        # Mock generate_content response
        mock_fc = MagicMock()
        mock_fc.id = "call_gemini_1"
        mock_fc.name = "get_current_weather"
        mock_fc.args = {"location": "Tokyo"}

        mock_genai_resp = MagicMock()
        mock_genai_resp.text = None
        mock_genai_resp.function_calls = [mock_fc]
        mock_genai_resp.usage_metadata = MagicMock(
            prompt_token_count=10,
            candidates_token_count=20,
            total_token_count=30,
        )

        mock_aio_models.generate_content = AsyncMock(return_value=mock_genai_resp)
        provider_svc.gemini_client = mock_gemini_client

        req = ChatCompletionRequest(
            service_name="gemini",
            model="gemini-3.8-flash",
            messages=[
                ChatMessage(role="user", content="Tokyo weather?"),
            ],
            tools=[self.sample_tool],
            tool_choice="auto",
        )

        resp = await provider_svc.generate_gemini_completion(req)
        self.assertEqual(resp.choices[0].finish_reason, "tool_calls")
        self.assertEqual(len(resp.choices[0].message.tool_calls), 1)
        self.assertEqual(
            resp.choices[0].message.tool_calls[0].function.name, "get_current_weather"
        )
        self.assertEqual(
            json.loads(resp.choices[0].message.tool_calls[0].function.arguments),
            {"location": "Tokyo"},
        )
        self.assertEqual(resp.usage.total_tokens, 30)

        # Verify config passed to gemini_client.aio.models.generate_content
        call_kwargs = mock_aio_models.generate_content.call_args.kwargs
        config_passed = call_kwargs["config"]
        self.assertIsNotNone(config_passed.tools)
        self.assertIsNotNone(config_passed.tool_config)


if __name__ == "__main__":
    unittest.main()
