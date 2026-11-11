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

    def test_prepare_claude_payload_consecutive_messages(self):
        provider = LlmProviderService()
        provider.claude_api_key = "test-key"

        req = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[
                ChatMessage(role="user", content="User message 1"),
                ChatMessage(role="user", content="User message 2"),
                ChatMessage(role="assistant", content="Assistant 1"),
                ChatMessage(role="assistant", content="Assistant 2"),
                ChatMessage(role="tool", content="Tool 1", tool_call_id="call_1"),
                ChatMessage(role="tool", content="Tool 2", tool_call_id="call_2"),
            ],
        )

        url, headers, payload = provider._prepare_claude_payload(req)
        msgs = payload["messages"]
        # user messages merged, assistant merged, tools merged into user
        self.assertEqual(len(msgs), 3)

    async def test_generate_claude_stream_events_and_errors(self):
        provider = LlmProviderService()
        provider.claude_api_key = None

        req = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="Hi")],
        )

        # Missing key raises 503
        with self.assertRaises(HTTPException) as ctx:
            async for _ in provider.generate_claude_stream(req):
                pass
        self.assertEqual(ctx.exception.status_code, 503)

        provider.claude_api_key = "test-key"

        # Mock stream lines
        lines = [
            'event: message_start\ndata: {"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 10}}}',
            'event: content_block_start\ndata: {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "Let me think"}}',
            'event: content_block_delta\ndata: {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "...done"}}',
            'event: content_block_start\ndata: {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": "Hello "}}',
            'event: content_block_delta\ndata: {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "world"}}',
            'event: content_block_start\ndata: {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "tc_1", "name": "test_tool"}}',
            'event: content_block_delta\ndata: {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": "{\\"a\\": 1}"}}',
            'event: message_delta\ndata: {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 15}}',
            'event: message_stop\ndata: {"type": "message_stop"}',
        ]

        class FakeClaudeStreamResponse:
            status_code = 200

            async def aiter_lines(self):
                for line in lines:
                    for subline in line.split("\n"):
                        yield subline

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class FakeClaudeClient:
            def stream(self, *args, **kwargs):
                return FakeClaudeStreamResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=FakeClaudeClient()):
            chunks = [c async for c in provider.generate_claude_stream(req)]
            self.assertGreater(len(chunks), 4)

        # Stream error event
        class FakeErrorStreamResponse:
            status_code = 200

            async def aiter_lines(self):
                yield "event: error"
                yield 'data: {"type": "error", "error": {"message": "Overloaded"}}'

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class FakeErrorClient:
            def stream(self, *args, **kwargs):
                return FakeErrorStreamResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=FakeErrorClient()):
            with self.assertRaises(HTTPException) as ctx:
                async for _ in provider.generate_claude_stream(req):
                    pass
            self.assertEqual(ctx.exception.status_code, 502)
            self.assertIn("Overloaded", ctx.exception.detail)

    async def test_prepare_claude_payload_thinking_and_tool_choices(self):
        provider = LlmProviderService()
        provider.claude_api_key = "test-key"

        req = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="hello")],
            thinking=True,
            thinking_budget=4000,
            max_tokens=2000,
            stop=["END"],
            tools=[self.sample_tool],
            tool_choice="required",
        )
        _, _, payload = provider._prepare_claude_payload(req)
        self.assertEqual(payload["thinking"]["type"], "enabled")
        self.assertEqual(payload["thinking"]["budget_tokens"], 4000)
        self.assertEqual(payload["max_tokens"], 6048)  # budget + 2048
        self.assertEqual(payload["stop_sequences"], ["END"])
        self.assertEqual(payload["tool_choice"]["type"], "any")

        # tool_choice as dict
        req_fn = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="hello")],
            tools=[self.sample_tool],
            tool_choice={"function": {"name": "get_current_weather"}},
        )
        _, _, payload_fn = provider._prepare_claude_payload(req_fn)
        self.assertEqual(payload_fn["tool_choice"]["type"], "tool")
        self.assertEqual(payload_fn["tool_choice"]["name"], "get_current_weather")

    async def test_generate_claude_completion_stop_reasons_and_errors(self):
        provider = LlmProviderService()
        provider.claude_api_key = "test-key"

        req = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="hello")],
        )

        # max_tokens stop reason
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "id": "msg_len",
            "model": "claude-3-5-sonnet-20241022",
            "content": [{"type": "text", "text": "truncated"}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 10, "output_tokens": 100},
        }

        with patch(
            "httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp
        ):
            resp = await provider.generate_claude_completion(req)
            self.assertEqual(resp.choices[0].finish_reason, "length")

        # stop_sequence stop reason
        mock_resp.json.return_value["stop_reason"] = "stop_sequence"
        with patch(
            "httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp
        ):
            resp = await provider.generate_claude_completion(req)
            self.assertEqual(resp.choices[0].finish_reason, "stop")

        # generic exception raises 502
        with patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection dropped"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await provider.generate_claude_completion(req)
            self.assertEqual(ctx.exception.status_code, 502)

    async def test_generate_claude_stream_error_status_and_exceptions(self):
        provider = LlmProviderService()
        provider.claude_api_key = "test-key"

        req = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="hello")],
        )

        # Response status >= 400
        class Fake400Response:
            status_code = 400
            text = '{"error": {"message": "Invalid request"}}'

            async def aread(self):
                pass

            def json(self):
                return {"error": {"message": "Invalid request"}}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class Fake400Client:
            def stream(self, *args, **kwargs):
                return Fake400Response()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=Fake400Client()):
            with self.assertRaises(HTTPException) as ctx:
                async for _ in provider.generate_claude_stream(req):
                    pass
            self.assertEqual(ctx.exception.status_code, 400)

        # Generic stream exception
        class FakeCrashClient:
            def stream(self, *args, **kwargs):
                raise RuntimeError("Stream broken")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=FakeCrashClient()):
            with self.assertRaises(HTTPException) as ctx:
                async for _ in provider.generate_claude_stream(req):
                    pass
            self.assertEqual(ctx.exception.status_code, 502)

    async def test_generate_claude_stream_full_events_tool_and_thinking(self):
        provider = LlmProviderService()
        provider.claude_api_key = "test-key"

        req = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="hello")],
        )

        sse_events = [
            "event: message_start\ndata: "
            + json.dumps(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_stream_99",
                        "model": "claude-3-5-sonnet-20241022",
                        "usage": {"input_tokens": 12},
                    },
                }
            )
            + "\n\n",
            "event: content_block_start\ndata: "
            + json.dumps(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tool_call_abc",
                        "name": "do_search",
                    },
                }
            )
            + "\n\n",
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"query": "antigravity"}',
                    },
                }
            )
            + "\n\n",
            "event: content_block_start\ndata: "
            + json.dumps(
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "thinking"},
                }
            )
            + "\n\n",
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {
                        "type": "thinking_delta",
                        "thinking": "Let me think about that",
                    },
                }
            )
            + "\n\n",
            "event: message_delta\ndata: "
            + json.dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "max_tokens"},
                    "usage": {"output_tokens": 40},
                }
            )
            + "\n\n",
            "event: message_stop\ndata: "
            + json.dumps({"type": "message_stop"})
            + "\n\n",
        ]

        class FakeStreamResponse:
            status_code = 200

            async def aread(self):
                pass

            async def aiter_lines(self):
                for line in "".join(sse_events).split("\n"):
                    yield line

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class FakeStreamClient:
            def stream(self, *args, **kwargs):
                return FakeStreamResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=FakeStreamClient()):
            chunks = [c async for c in provider.generate_claude_stream(req)]
            self.assertGreater(len(chunks), 3)

            # Find tool use chunk
            tool_start = next(
                (
                    c
                    for c in chunks
                    if c.choices
                    and c.choices[0].delta
                    and c.choices[0].delta.tool_calls
                    and c.choices[0].delta.tool_calls[0].function.name == "do_search"
                ),
                None,
            )
            self.assertIsNotNone(tool_start)

            # Find thinking delta chunk
            thinking_chunk = next(
                (
                    c
                    for c in chunks
                    if c.choices
                    and c.choices[0].delta
                    and c.choices[0].delta.reasoning_content
                ),
                None,
            )
            self.assertIsNotNone(thinking_chunk)
            self.assertEqual(
                thinking_chunk.choices[0].delta.reasoning_content,
                "Let me think about that",
            )

            # Final finish reason should be length for max_tokens
            last_chunk = chunks[-1]
            self.assertEqual(last_chunk.choices[0].finish_reason, "length")


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
