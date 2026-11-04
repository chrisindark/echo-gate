import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException

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


class TestClaudeProvider(unittest.IsolatedAsyncioTestCase):
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

    async def test_generate_claude_completion_success(self):
        provider = LlmProviderService()
        provider.claude_api_key = "test-ant-key"
        provider.claude_base_url = "https://api.anthropic.com/v1/messages"

        mock_resp_data = {
            "id": "msg_12345",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-sonnet-20241022",
            "content": [{"type": "text", "text": "Hello from Claude!"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 15, "output_tokens": 25},
        }

        mock_http_response = MagicMock(spec=httpx.Response)
        mock_http_response.status_code = 200
        mock_http_response.json.return_value = mock_resp_data
        mock_http_response.raise_for_status = MagicMock()

        request = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[
                ChatMessage(role="system", content="You are a helpful assistant."),
                ChatMessage(role="user", content="Hi there!"),
            ],
            temperature=0.5,
            max_tokens=1000,
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_http_response

            resp = await provider.generate_claude_completion(request)

            self.assertEqual(resp.id, "msg_12345")
            self.assertEqual(resp.model, "claude-3-5-sonnet-20241022")
            self.assertEqual(resp.choices[0].message.content, "Hello from Claude!")
            self.assertEqual(resp.choices[0].finish_reason, "stop")
            self.assertEqual(resp.usage.prompt_tokens, 15)
            self.assertEqual(resp.usage.completion_tokens, 25)
            self.assertEqual(resp.usage.total_tokens, 40)

            # Check outbound payload to Claude
            mock_post.assert_awaited_once()
            call_kwargs = mock_post.call_args.kwargs
            payload = call_kwargs["json"]
            headers = call_kwargs["headers"]

            self.assertEqual(headers["x-api-key"], "test-ant-key")
            self.assertEqual(headers["anthropic-version"], "2023-06-01")
            self.assertEqual(payload["system"], "You are a helpful assistant.")
            self.assertEqual(payload["max_tokens"], 1000)
            self.assertEqual(payload["temperature"], 0.5)
            self.assertEqual(
                payload["messages"],
                [{"role": "user", "content": [{"type": "text", "text": "Hi there!"}]}],
            )

    async def test_generate_claude_completion_with_tools_and_tool_call(self):
        provider = LlmProviderService()
        provider.claude_api_key = "test-ant-key"
        provider.claude_base_url = "https://api.anthropic.com/v1"

        mock_resp_data = {
            "id": "msg_tool_call_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-sonnet-20241022",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_abc123",
                    "name": "get_current_weather",
                    "input": {"location": "San Francisco, CA", "unit": "celsius"},
                }
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 50, "output_tokens": 30},
        }

        mock_http_response = MagicMock(spec=httpx.Response)
        mock_http_response.status_code = 200
        mock_http_response.json.return_value = mock_resp_data
        mock_http_response.raise_for_status = MagicMock()

        request = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="Weather in SF?")],
            tools=[self.sample_tool],
            tool_choice="auto",
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_http_response

            resp = await provider.generate_claude_completion(request)

            self.assertEqual(resp.choices[0].finish_reason, "tool_calls")
            self.assertIsNotNone(resp.choices[0].message.tool_calls)
            self.assertEqual(len(resp.choices[0].message.tool_calls), 1)

            tc = resp.choices[0].message.tool_calls[0]
            self.assertEqual(tc.id, "toolu_abc123")
            self.assertEqual(tc.function.name, "get_current_weather")
            self.assertEqual(
                json.loads(tc.function.arguments),
                {"location": "San Francisco, CA", "unit": "celsius"},
            )

            # URL normalization check
            call_url = mock_post.call_args.args[0]
            self.assertEqual(call_url, "https://api.anthropic.com/v1/messages")

            # Check tools translation in request payload
            payload = mock_post.call_args.kwargs["json"]
            self.assertEqual(len(payload["tools"]), 1)
            self.assertEqual(payload["tools"][0]["name"], "get_current_weather")
            self.assertEqual(
                payload["tools"][0]["input_schema"],
                self.sample_tool.function.parameters,
            )
            self.assertEqual(payload["tool_choice"], {"type": "auto"})

    async def test_generate_claude_completion_tool_results_and_role_alternation(self):
        provider = LlmProviderService()
        provider.claude_api_key = "test-ant-key"

        mock_resp_data = {
            "id": "msg_tool_resp",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-sonnet-20241022",
            "content": [{"type": "text", "text": "It is 20 degrees Celsius in SF."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }

        mock_http_response = MagicMock(spec=httpx.Response)
        mock_http_response.status_code = 200
        mock_http_response.json.return_value = mock_resp_data
        mock_http_response.raise_for_status = MagicMock()

        request = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[
                ChatMessage(role="user", content="Weather in SF?"),
                ChatMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="toolu_1",
                            type="function",
                            function=FunctionCall(
                                name="get_current_weather",
                                arguments='{"location":"SF"}',
                            ),
                        )
                    ],
                ),
                ChatMessage(
                    role="tool",
                    tool_call_id="toolu_1",
                    content='{"temperature": 20, "unit": "celsius"}',
                ),
            ],
            tools=[self.sample_tool],
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_http_response

            resp = await provider.generate_claude_completion(request)

            self.assertEqual(
                resp.choices[0].message.content, "It is 20 degrees Celsius in SF."
            )

            payload = mock_post.call_args.kwargs["json"]
            messages = payload["messages"]
            # Verify roles alternate properly: user -> assistant -> user (with tool_result)
            self.assertEqual(len(messages), 3)
            self.assertEqual(messages[0]["role"], "user")
            self.assertEqual(messages[1]["role"], "assistant")
            self.assertEqual(messages[1]["content"][0]["type"], "tool_use")
            self.assertEqual(messages[2]["role"], "user")
            self.assertEqual(messages[2]["content"][0]["type"], "tool_result")
            self.assertEqual(messages[2]["content"][0]["tool_use_id"], "toolu_1")

    async def test_generate_claude_completion_missing_key(self):
        provider = LlmProviderService()
        provider.claude_api_key = None

        request = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="Hi")],
        )

        with self.assertRaises(HTTPException) as ctx:
            await provider.generate_claude_completion(request)

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("Claude API key is not configured", ctx.exception.detail)

    async def test_generate_claude_completion_http_error(self):
        provider = LlmProviderService()
        provider.claude_api_key = "test-ant-key"

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        err_resp = httpx.Response(
            400,
            request=req,
            json={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Prompt is too long",
                },
            },
        )

        request = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="Too long")],
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = err_resp

            with self.assertRaises(HTTPException) as ctx:
                await provider.generate_claude_completion(request)

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("Claude LLM error: Prompt is too long", ctx.exception.detail)

    async def test_generate_completion_dispatch(self):
        provider = LlmProviderService()
        mock_claude = AsyncMock()
        provider.generate_claude_completion = mock_claude

        request = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="Hi")],
        )

        await provider.generate_completion(request, service_name="claude")
        mock_claude.assert_awaited_once_with(request)

        mock_claude.reset_mock()
        await provider.generate_completion(request, service_name="anthropic")
        mock_claude.assert_awaited_once_with(request)

    async def test_router_service_claude_dispatch(self):
        mock_provider = MagicMock(spec=LlmProviderService)
        mock_provider.generate_claude_completion = AsyncMock(
            return_value=MagicMock(model="claude-3-5-sonnet-20241022")
        )

        router_svc = LlmRouterService(
            llm_provider_service=mock_provider,
            embedding_service=MagicMock(),
            llm_cache_service=MagicMock(),
            redis_service=MagicMock(),
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=MagicMock(),
        )

        request = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="Hi")],
        )

        resp, svc, model = await router_svc._call_provider_with_retry(
            request=request,
            service_name="claude",
            timeout_seconds=10.0,
            max_retries=0,
        )

        self.assertEqual(svc, "claude")
        self.assertEqual(model, "claude-3-5-sonnet-20241022")
        mock_provider.generate_claude_completion.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
