import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import config
from app.core.constants import MOCK_RESPONSE_TEXT
from app.core.dependencies import get_llm_router_service
from app.core.guards import llm_quota_guard
from app.main import app
from app.modules.chat.chat_schema import (
    SSE_DONE_EVENT,
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChunkChoice,
    ChunkDelta,
    FunctionCall,
    FunctionCallChunk,
    FunctionDefinition,
    StreamAccumulator,
    Tool,
    ToolCall,
    ToolCallChunk,
    Usage,
    format_sse_event,
    response_to_stream_chunks,
)
from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.llm.llm_router_service import LlmRouterService


class TestStreamingSupportPhase1(unittest.TestCase):
    def test_chunk_serialization(self):
        chunk = ChatCompletionChunk(
            id="chatcmpl-stream-1",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChunkDelta(content="Hello"),
                    finish_reason=None,
                )
            ],
        )
        json_str = chunk.model_dump_json(exclude_none=True)
        data = json.loads(json_str)

        self.assertEqual(data["id"], "chatcmpl-stream-1")
        self.assertEqual(data["object"], "chat.completion.chunk")
        self.assertEqual(data["created"], 1700000000)
        self.assertEqual(data["model"], "gpt-4o-mini")
        self.assertEqual(len(data["choices"]), 1)
        self.assertEqual(data["choices"][0]["delta"]["content"], "Hello")
        self.assertNotIn("role", data["choices"][0]["delta"])
        self.assertNotIn("finish_reason", data["choices"][0])

    def test_format_sse_event(self):
        chunk = ChatCompletionChunk(
            id="chatcmpl-stream-1",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChunkDelta(role="assistant"),
                )
            ],
        )
        sse_line = format_sse_event(chunk)
        self.assertTrue(sse_line.startswith("data: "))
        self.assertTrue(sse_line.endswith("\n\n"))

        inner_json = json.loads(sse_line[len("data: ") : -2])
        self.assertEqual(inner_json["choices"][0]["delta"]["role"], "assistant")

        self.assertEqual(SSE_DONE_EVENT, "data: [DONE]\n\n")

    def test_response_to_stream_chunks_text(self):
        resp = ChatCompletionResponse(
            id="chatcmpl-cached-1",
            created=1700000000,
            model="gemini-3.8-flash",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant", content="Streaming cached reply"
                    ),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=4, total_tokens=14),
            cache_info={"source": "redis", "cache_hit": True},
        )

        chunks = response_to_stream_chunks(resp)
        self.assertEqual(len(chunks), 3)

        # 1. Role chunk
        self.assertEqual(chunks[0].choices[0].delta.role, "assistant")
        self.assertIsNone(chunks[0].choices[0].delta.content)
        self.assertIsNone(chunks[0].choices[0].finish_reason)

        # 2. Content chunk
        self.assertEqual(chunks[1].choices[0].delta.content, "Streaming cached reply")
        self.assertIsNone(chunks[1].choices[0].finish_reason)

        # 3. Finish chunk
        self.assertEqual(chunks[2].choices[0].finish_reason, "stop")
        self.assertIsNotNone(chunks[2].usage)
        self.assertEqual(chunks[2].usage.total_tokens, 14)

    def test_response_to_stream_chunks_tools(self):
        resp = ChatCompletionResponse(
            id="chatcmpl-cached-tool",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content=None,
                        tool_calls=[
                            ToolCall(
                                id="call_abc",
                                type="function",
                                function=FunctionCall(
                                    name="get_weather",
                                    arguments='{"city": "Paris"}',
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
        )

        chunks = response_to_stream_chunks(resp)
        self.assertEqual(len(chunks), 3)

        # 1. Role chunk
        self.assertEqual(chunks[0].choices[0].delta.role, "assistant")

        # 2. Tool call chunk
        tool_delta = chunks[1].choices[0].delta.tool_calls
        self.assertEqual(len(tool_delta), 1)
        self.assertEqual(tool_delta[0].id, "call_abc")
        self.assertEqual(tool_delta[0].function.name, "get_weather")
        self.assertEqual(tool_delta[0].function.arguments, '{"city": "Paris"}')

        # 3. Finish chunk
        self.assertEqual(chunks[2].choices[0].finish_reason, "tool_calls")

    def test_stream_accumulator_text(self):
        acc = StreamAccumulator(model="qwen3:8b", request_id="chatcmpl-acc-1")

        chunks = [
            ChatCompletionChunk(
                id="chatcmpl-acc-1",
                created=1700000000,
                model="qwen3:8b",
                choices=[ChunkChoice(index=0, delta=ChunkDelta(role="assistant"))],
            ),
            ChatCompletionChunk(
                id="chatcmpl-acc-1",
                created=1700000000,
                model="qwen3:8b",
                choices=[
                    ChunkChoice(
                        index=0, delta=ChunkDelta(content="The capital of France ")
                    )
                ],
            ),
            ChatCompletionChunk(
                id="chatcmpl-acc-1",
                created=1700000000,
                model="qwen3:8b",
                choices=[ChunkChoice(index=0, delta=ChunkDelta(content="is Paris."))],
            ),
            ChatCompletionChunk(
                id="chatcmpl-acc-1",
                created=1700000000,
                model="qwen3:8b",
                choices=[
                    ChunkChoice(index=0, delta=ChunkDelta(), finish_reason="stop")
                ],
                usage=Usage(prompt_tokens=8, completion_tokens=6, total_tokens=14),
            ),
        ]

        for c in chunks:
            acc.add_chunk(c)

        assembled = acc.build_response()
        self.assertEqual(assembled.id, "chatcmpl-acc-1")
        self.assertEqual(assembled.model, "qwen3:8b")
        self.assertEqual(assembled.choices[0].message.role, "assistant")
        self.assertEqual(
            assembled.choices[0].message.content, "The capital of France is Paris."
        )
        self.assertEqual(assembled.choices[0].finish_reason, "stop")
        self.assertEqual(assembled.usage.total_tokens, 14)

    def test_stream_accumulator_chunked_tool_calls(self):
        acc = StreamAccumulator(model="gpt-4o-mini")

        chunks = [
            ChatCompletionChunk(
                id="chatcmpl-tool-acc",
                created=1700000000,
                model="gpt-4o-mini",
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(
                            role="assistant",
                            tool_calls=[
                                ToolCallChunk(
                                    index=0,
                                    id="call_999",
                                    function=FunctionCallChunk(
                                        name="fetch_stock_price"
                                    ),
                                )
                            ],
                        ),
                    )
                ],
            ),
            ChatCompletionChunk(
                id="chatcmpl-tool-acc",
                created=1700000000,
                model="gpt-4o-mini",
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(
                            tool_calls=[
                                ToolCallChunk(
                                    index=0,
                                    function=FunctionCallChunk(arguments='{"ticker":'),
                                )
                            ]
                        ),
                    )
                ],
            ),
            ChatCompletionChunk(
                id="chatcmpl-tool-acc",
                created=1700000000,
                model="gpt-4o-mini",
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(
                            tool_calls=[
                                ToolCallChunk(
                                    index=0,
                                    function=FunctionCallChunk(arguments=' "GOOG"}'),
                                )
                            ]
                        ),
                        finish_reason="tool_calls",
                    )
                ],
                usage=Usage(prompt_tokens=12, completion_tokens=10, total_tokens=22),
            ),
        ]

        for c in chunks:
            acc.add_chunk(c)

        assembled = acc.build_response()
        self.assertEqual(assembled.id, "chatcmpl-tool-acc")
        self.assertIsNone(assembled.choices[0].message.content)
        self.assertEqual(len(assembled.choices[0].message.tool_calls), 1)

        tool_call = assembled.choices[0].message.tool_calls[0]
        self.assertEqual(tool_call.id, "call_999")
        self.assertEqual(tool_call.function.name, "fetch_stock_price")
        self.assertEqual(tool_call.function.arguments, '{"ticker": "GOOG"}')
        self.assertEqual(assembled.choices[0].finish_reason, "tool_calls")
        self.assertEqual(assembled.usage.total_tokens, 22)

    def test_stream_accumulator_dict_inputs(self):
        acc = StreamAccumulator()
        chunk_dict = {
            "id": "chatcmpl-dict-test",
            "created": 1700000000,
            "model": "claude-3-5-sonnet",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "Direct dict payload"},
                    "finish_reason": "stop",
                }
            ],
        }
        acc.add_chunk(chunk_dict)
        assembled = acc.build_response()
        self.assertEqual(assembled.id, "chatcmpl-dict-test")
        self.assertEqual(assembled.choices[0].message.content, "Direct dict payload")
        self.assertEqual(assembled.choices[0].finish_reason, "stop")


class TestStreamingSupportPhase2(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = LlmProviderService()

    async def test_mock_streaming_text(self):
        req = ChatCompletionRequest(
            service_name="mock",
            model="mock-model",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        chunks = []
        async for chunk in self.service.generate_mock_stream(req):
            chunks.append(chunk)

        self.assertGreaterEqual(len(chunks), 3)
        self.assertEqual(chunks[0].choices[0].delta.role, "assistant")
        self.assertEqual(chunks[-1].choices[0].finish_reason, "stop")

        acc = StreamAccumulator(model="mock-model")
        for c in chunks:
            acc.add_chunk(c)
        assembled = acc.build_response()
        self.assertEqual(assembled.choices[0].message.content, MOCK_RESPONSE_TEXT)
        self.assertEqual(assembled.choices[0].finish_reason, "stop")

    async def test_mock_streaming_tools(self):
        req = ChatCompletionRequest(
            service_name="mock",
            model="mock-model",
            messages=[ChatMessage(role="user", content="What's the weather?")],
            tools=[
                Tool(
                    type="function",
                    function=FunctionDefinition(
                        name="get_weather", description="Weather lookup"
                    ),
                )
            ],
        )
        chunks = []
        async for chunk in self.service.generate_mock_stream(req):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].choices[0].delta.role, "assistant")
        self.assertEqual(
            chunks[1].choices[0].delta.tool_calls[0].function.name,
            "get_weather",
        )
        self.assertEqual(chunks[2].choices[0].finish_reason, "tool_calls")

        acc = StreamAccumulator(model="mock-model")
        for c in chunks:
            acc.add_chunk(c)
        assembled = acc.build_response()
        self.assertEqual(len(assembled.choices[0].message.tool_calls), 1)
        self.assertEqual(
            assembled.choices[0].message.tool_calls[0].function.name,
            "get_weather",
        )
        self.assertEqual(assembled.choices[0].finish_reason, "tool_calls")

    async def test_stream_openai_compatible_success(self):
        sse_lines = [
            'data: {"id": "chatcmpl-mock-1", "created": 1700000000, "model": "gpt-4o", "choices": [{"index": 0, "delta": {"role": "assistant"}}]}\n',
            'data: {"id": "chatcmpl-mock-1", "created": 1700000000, "model": "gpt-4o", "choices": [{"index": 0, "delta": {"content": "Hello"}}]}\n',
            'data: {"id": "chatcmpl-mock-1", "created": 1700000000, "model": "gpt-4o", "choices": [{"index": 0, "delta": {"content": " world"}}]}\n',
            'data: {"id": "chatcmpl-mock-1", "created": 1700000000, "model": "gpt-4o", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}\n',
            "data: [DONE]\n",
        ]

        class FakeAsyncStream:
            def __init__(self, lines):
                self.lines = lines
                self.status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            async def aiter_lines(self):
                for line in self.lines:
                    yield line

        class FakeAsyncClient:
            def __init__(self, lines):
                self.lines = lines

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            def stream(self, *args, **kwargs):
                return FakeAsyncStream(self.lines)

        with patch("httpx.AsyncClient", return_value=FakeAsyncClient(sse_lines)):
            chunks = []
            async for chunk in self.service._stream_openai_compatible(
                "http://test.url", {}, {"model": "gpt-4o"}, "TestProvider"
            ):
                chunks.append(chunk)

            self.assertEqual(len(chunks), 4)
            acc = StreamAccumulator(model="gpt-4o")
            for c in chunks:
                acc.add_chunk(c)
            assembled = acc.build_response()
            self.assertEqual(assembled.choices[0].message.content, "Hello world")
            self.assertEqual(assembled.choices[0].finish_reason, "stop")

    async def test_stream_openai_compatible_error(self):
        class FakeErrorStream:
            def __init__(self):
                self.status_code = 401
                self.text = '{"error": {"message": "Invalid API key"}}'

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            async def aread(self):
                return self.text.encode()

            def json(self):
                return {"error": {"message": "Invalid API key"}}

        class FakeErrorClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            def stream(self, *args, **kwargs):
                return FakeErrorStream()

        with patch("httpx.AsyncClient", return_value=FakeErrorClient()):
            with self.assertRaises(HTTPException) as ctx:
                async for _ in self.service._stream_openai_compatible(
                    "http://test.url", {}, {"model": "gpt-4o"}, "TestProvider"
                ):
                    pass
            self.assertEqual(ctx.exception.status_code, 401)
            self.assertIn("Invalid API key", ctx.exception.detail)

    async def test_claude_streaming_text(self):
        self.service.claude_api_key = "fake-claude-key"
        sse_lines = [
            "event: message_start\n",
            'data: {"type": "message_start", "message": {"id": "msg_c1", "type": "message", "role": "assistant", "model": "claude-3-5-sonnet", "usage": {"input_tokens": 15, "output_tokens": 1}}}\n',
            "event: content_block_start\n",
            'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}\n',
            "event: content_block_delta\n",
            'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Bonjour le monde"}}\n',
            "event: content_block_stop\n",
            'data: {"type": "content_block_stop", "index": 0}\n',
            "event: message_delta\n",
            'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 8}}\n',
            "event: message_stop\n",
            'data: {"type": "message_stop"}\n',
        ]

        class FakeAsyncStream:
            def __init__(self, lines):
                self.lines = lines
                self.status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            async def aiter_lines(self):
                for line in self.lines:
                    yield line

        class FakeAsyncClient:
            def __init__(self, lines):
                self.lines = lines

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            def stream(self, *args, **kwargs):
                return FakeAsyncStream(self.lines)

        req = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[
                ChatMessage(role="user", content="Translate hello world to French")
            ],
        )

        with patch("httpx.AsyncClient", return_value=FakeAsyncClient(sse_lines)):
            chunks = []
            async for chunk in self.service.generate_claude_stream(req):
                chunks.append(chunk)

            self.assertEqual(len(chunks), 3)  # role, content, finish
            self.assertEqual(chunks[0].choices[0].delta.role, "assistant")
            self.assertEqual(chunks[1].choices[0].delta.content, "Bonjour le monde")
            self.assertEqual(chunks[2].choices[0].finish_reason, "stop")
            self.assertEqual(chunks[2].usage.prompt_tokens, 15)
            self.assertEqual(chunks[2].usage.completion_tokens, 8)
            self.assertEqual(chunks[2].usage.total_tokens, 23)

    async def test_claude_streaming_tools(self):
        self.service.claude_api_key = "fake-claude-key"
        sse_lines = [
            "event: message_start\n",
            'data: {"type": "message_start", "message": {"id": "msg_tool_1", "role": "assistant", "model": "claude-3-5-sonnet-20241022", "usage": {"input_tokens": 20}}}\n',
            "event: content_block_start\n",
            'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_abc", "name": "get_stock_quote", "input": {}}}\n',
            "event: content_block_delta\n",
            'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{\\"symbol\\": "}}\n',
            "event: content_block_delta\n",
            'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "\\"AAPL\\"}"}}\n',
            "event: content_block_stop\n",
            'data: {"type": "content_block_stop", "index": 0}\n',
            "event: message_delta\n",
            'data: {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 12}}\n',
            "event: message_stop\n",
            'data: {"type": "message_stop"}\n',
        ]

        class FakeAsyncStream:
            def __init__(self, lines):
                self.lines = lines
                self.status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            async def aiter_lines(self):
                for line in self.lines:
                    yield line

        class FakeAsyncClient:
            def __init__(self, lines):
                self.lines = lines

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

            def stream(self, *args, **kwargs):
                return FakeAsyncStream(self.lines)

        req = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="Price of AAPL")],
        )

        with patch("httpx.AsyncClient", return_value=FakeAsyncClient(sse_lines)):
            chunks = []
            async for chunk in self.service.generate_claude_stream(req):
                chunks.append(chunk)

            acc = StreamAccumulator(model="claude-3-5-sonnet-20241022")
            for c in chunks:
                acc.add_chunk(c)
            assembled = acc.build_response()

            self.assertEqual(len(assembled.choices[0].message.tool_calls), 1)
            tc = assembled.choices[0].message.tool_calls[0]
            self.assertEqual(tc.id, "toolu_abc")
            self.assertEqual(tc.function.name, "get_stock_quote")
            self.assertEqual(tc.function.arguments, '{"symbol": "AAPL"}')
            self.assertEqual(assembled.choices[0].finish_reason, "tool_calls")
            self.assertEqual(assembled.usage.total_tokens, 32)

    async def test_gemini_streaming_text_and_tools(self):
        from google.genai.types import (
            Candidate,
            Content,
            FinishReason,
            FunctionCall,
            GenerateContentResponse,
            GenerateContentResponseUsageMetadata,
            Part,
        )

        chunk1 = GenerateContentResponse(
            candidates=[
                Candidate(
                    content=Content(parts=[Part(text="Analyzing portfolio...")]),
                )
            ]
        )
        chunk2 = GenerateContentResponse(
            candidates=[
                Candidate(
                    content=Content(
                        parts=[
                            Part(
                                function_call=FunctionCall(
                                    name="calc_metrics", args={"risk": "low"}
                                )
                            )
                        ]
                    ),
                    finish_reason=FinishReason.STOP,
                )
            ],
            usage_metadata=GenerateContentResponseUsageMetadata(
                prompt_token_count=18,
                candidates_token_count=10,
                total_token_count=28,
            ),
        )

        async def fake_gemini_stream(*args, **kwargs):
            for ch in [chunk1, chunk2]:
                yield ch

        mock_client = MagicMock()
        mock_client.aio.models.generate_content_stream = AsyncMock(
            side_effect=fake_gemini_stream
        )
        self.service.gemini_client = mock_client

        req = ChatCompletionRequest(
            service_name="gemini",
            model="gemini-3.8-flash",
            messages=[ChatMessage(role="user", content="Analyze portfolio")],
        )

        chunks = []
        async for chunk in self.service.generate_gemini_stream(req):
            chunks.append(chunk)

        self.assertGreaterEqual(len(chunks), 4)  # role, text, tool_call, finish
        acc = StreamAccumulator(model="gemini-3.8-flash")
        for c in chunks:
            acc.add_chunk(c)
        assembled = acc.build_response()

        self.assertEqual(assembled.choices[0].message.content, "Analyzing portfolio...")
        self.assertEqual(len(assembled.choices[0].message.tool_calls), 1)
        self.assertEqual(
            assembled.choices[0].message.tool_calls[0].function.name,
            "calc_metrics",
        )
        self.assertEqual(assembled.choices[0].finish_reason, "tool_calls")
        self.assertEqual(assembled.usage.total_tokens, 28)

    async def test_generate_stream_dispatcher(self):
        req = ChatCompletionRequest(
            service_name="mock",
            model="mock-model",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        chunks = []
        async for c in self.service.generate_stream(req, service_name="mock"):
            chunks.append(c)
        self.assertTrue(len(chunks) > 0)

        with self.assertRaises(HTTPException) as ctx:
            async for _ in self.service.generate_stream(
                req, service_name="invalid-provider"
            ):
                pass
        self.assertEqual(ctx.exception.status_code, 400)


class TestStreamAccumulatorGuardrails(unittest.TestCase):
    def test_default_guardrail_value(self):
        acc = StreamAccumulator(model="gpt-4o")
        self.assertEqual(acc.max_content_bytes, 2 * 1024 * 1024)
        self.assertFalse(acc.overflowed)
        self.assertEqual(acc.current_content_bytes, 0)

    def test_text_overflow_guardrail(self):
        acc = StreamAccumulator(model="gpt-4o", max_content_bytes=15)

        chunk1 = ChatCompletionChunk(
            id="c1",
            created=1000,
            model="gpt-4o",
            choices=[ChunkChoice(index=0, delta=ChunkDelta(content="Hello 123"))],
        )
        acc.add_chunk(chunk1)
        self.assertFalse(acc.overflowed)
        self.assertEqual(acc.current_content_bytes, 9)
        self.assertEqual(acc.content_parts, ["Hello 123"])

        # Second chunk pushes past 15 bytes (9 + 10 = 19 > 15)
        chunk2 = ChatCompletionChunk(
            id="c1",
            created=1000,
            model="gpt-4o",
            choices=[ChunkChoice(index=0, delta=ChunkDelta(content="4567890ABC"))],
        )
        acc.add_chunk(chunk2)
        self.assertTrue(acc.overflowed)
        # Should not append overflowed content
        self.assertEqual(acc.content_parts, ["Hello 123"])

        resp = acc.build_response()
        self.assertEqual(resp.choices[0].finish_reason, "length")
        self.assertEqual(resp.choices[0].message.content, "Hello 123")

    def test_tool_arguments_overflow_guardrail(self):
        acc = StreamAccumulator(model="gpt-4o", max_content_bytes=20)
        chunk1 = ChatCompletionChunk(
            id="c1",
            created=1000,
            model="gpt-4o",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChunkDelta(
                        tool_calls=[
                            ToolCallChunk(
                                index=0,
                                id="call_1",
                                type="function",
                                function=FunctionCallChunk(
                                    name="search", arguments='{"q": '
                                ),
                            )
                        ]
                    ),
                )
            ],
        )
        acc.add_chunk(chunk1)
        self.assertFalse(acc.overflowed)

        # Huge argument chunk
        chunk2 = ChatCompletionChunk(
            id="c1",
            created=1000,
            model="gpt-4o",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChunkDelta(
                        tool_calls=[
                            ToolCallChunk(
                                index=0,
                                function=FunctionCallChunk(
                                    arguments='"very very very long search query string"}'
                                ),
                            )
                        ]
                    ),
                )
            ],
        )
        acc.add_chunk(chunk2)
        self.assertTrue(acc.overflowed)
        resp = acc.build_response()
        self.assertEqual(resp.choices[0].finish_reason, "length")


class TestLlmRouterServiceStreamingPhase3(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved_cache_mode = config.CACHE_MODE
        config.CACHE_MODE = "active"
        self.llm_provider_service = MagicMock(spec=LlmProviderService)
        self.embedding_service = MagicMock()
        self.llm_cache_service = MagicMock()
        self.redis_service = MagicMock()
        self.redis_service.get = AsyncMock(return_value=None)
        self.redis_service.set = AsyncMock(return_value=True)
        self.redis_service.delete = AsyncMock(return_value=True)
        self.reranker_service = MagicMock()
        self.intent_classifier_service = MagicMock()
        self.entity_extractor_service = MagicMock()
        self.llm_usage_service = MagicMock()
        self.gateway_requests_service = MagicMock()

        self.router_service = LlmRouterService(
            llm_provider_service=self.llm_provider_service,
            embedding_service=self.embedding_service,
            llm_cache_service=self.llm_cache_service,
            redis_service=self.redis_service,
            reranker_service=self.reranker_service,
            intent_classifier_service=self.intent_classifier_service,
            entity_extractor_service=self.entity_extractor_service,
            llm_usage_service=self.llm_usage_service,
            gateway_requests_service=self.gateway_requests_service,
        )

        self.created_background_tasks = []
        orig_create = self.router_service._create_background_task

        def record_bg(coro):
            task = orig_create(coro)
            self.created_background_tasks.append(task)
            return task

        self.router_service._create_background_task = MagicMock(side_effect=record_bg)

    async def asyncTearDown(self):
        config.CACHE_MODE = self._saved_cache_mode
        if self.created_background_tasks:
            import asyncio

            await asyncio.gather(*self.created_background_tasks, return_exceptions=True)

    async def test_router_stream_redis_exact_match(self):
        cached_resp = ChatCompletionResponse(
            id="chatcmpl-cached-redis",
            created=1700000000,
            model="claude-3-5-sonnet-20241022",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="From Redis"),
                    finish_reason="stop",
                )
            ],
            cache_info={"source": "redis", "cache_hit": True},
        )
        self.router_service._get_redis_exact_match = AsyncMock(return_value=cached_resp)

        req = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="Test redis exact")],
        )

        chunks = []
        async for chunk in self.router_service.generate_stream(req):
            chunks.append(chunk)

        self.assertGreaterEqual(len(chunks), 2)
        content_chunks = [c for c in chunks if c.choices and c.choices[0].delta.content]
        self.assertEqual(len(content_chunks), 1)
        self.assertEqual(content_chunks[0].choices[0].delta.content, "From Redis")
        self.llm_provider_service.generate_stream.assert_not_called()
        self.assertGreaterEqual(
            self.router_service._create_background_task.call_count, 2
        )

    async def test_router_stream_qdrant_exact_match(self):
        self.router_service._get_redis_exact_match = AsyncMock(return_value=None)
        cached_resp = ChatCompletionResponse(
            id="chatcmpl-cached-qdrant",
            created=1700000000,
            model="gemini-3.8-flash",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="From Qdrant"),
                    finish_reason="stop",
                )
            ],
            cache_info={"source": "qdrant_exact", "cache_hit": True},
        )
        self.router_service._get_qdrant_exact_match = AsyncMock(
            return_value=(cached_resp, "pt-123", 3600)
        )

        req = ChatCompletionRequest(
            service_name="gemini",
            model="gemini-3.8-flash",
            messages=[ChatMessage(role="user", content="Test qdrant exact")],
        )

        chunks = []
        async for chunk in self.router_service.generate_stream(req):
            chunks.append(chunk)

        self.assertGreaterEqual(len(chunks), 2)
        content_chunks = [c for c in chunks if c.choices and c.choices[0].delta.content]
        self.assertEqual(content_chunks[0].choices[0].delta.content, "From Qdrant")
        self.llm_provider_service.generate_stream.assert_not_called()

    async def test_router_stream_semantic_cache_hit(self):
        self.router_service._get_redis_exact_match = AsyncMock(return_value=None)
        self.router_service._get_qdrant_exact_match = AsyncMock(
            return_value=(None, None, None)
        )
        cached_resp = ChatCompletionResponse(
            id="chatcmpl-cached-semantic",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content="From Semantic"),
                    finish_reason="stop",
                )
            ],
            cache_info={"source": "qdrant_semantic", "cache_hit": True},
        )
        self.router_service.evaluate_semantic_cache = AsyncMock(
            return_value={"response": cached_resp, "latency_breakdown": {}}
        )

        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Test semantic cache")],
        )

        chunks = []
        async for chunk in self.router_service.generate_stream(req):
            chunks.append(chunk)

        self.assertGreaterEqual(len(chunks), 2)
        content_chunks = [c for c in chunks if c.choices and c.choices[0].delta.content]
        self.assertEqual(content_chunks[0].choices[0].delta.content, "From Semantic")
        self.llm_provider_service.generate_stream.assert_not_called()

    async def test_router_stream_provider_cache_miss_and_save(self):
        self.router_service._get_redis_exact_match = AsyncMock(return_value=None)
        self.router_service._get_qdrant_exact_match = AsyncMock(
            return_value=(None, None, None)
        )
        self.router_service.evaluate_semantic_cache = AsyncMock(
            return_value={"response": None, "latency_breakdown": {}}
        )
        self.router_service._background_save_cache = AsyncMock()

        async def fake_provider_stream(*args, **kwargs):
            yield ChatCompletionChunk(
                id="c-provider",
                created=1700000000,
                model="claude-3-5-sonnet-20241022",
                choices=[ChunkChoice(index=0, delta=ChunkDelta(role="assistant"))],
            )
            yield ChatCompletionChunk(
                id="c-provider",
                created=1700000000,
                model="claude-3-5-sonnet-20241022",
                choices=[
                    ChunkChoice(
                        index=0, delta=ChunkDelta(content="Provider streamed token")
                    )
                ],
            )
            yield ChatCompletionChunk(
                id="c-provider",
                created=1700000000,
                model="claude-3-5-sonnet-20241022",
                choices=[
                    ChunkChoice(index=0, delta=ChunkDelta(), finish_reason="stop")
                ],
            )

        self.llm_provider_service.generate_stream = MagicMock(
            side_effect=fake_provider_stream
        )

        req = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="Test cache miss stream")],
        )

        chunks = []
        async for chunk in self.router_service.generate_stream(req):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[1].choices[0].delta.content, "Provider streamed token")
        self.router_service._background_save_cache.assert_called_once()
        self.assertGreaterEqual(
            self.router_service._create_background_task.call_count, 3
        )

    async def test_router_stream_memory_overflow_guardrail_skips_cache(self):
        self.router_service._get_redis_exact_match = AsyncMock(return_value=None)
        self.router_service._get_qdrant_exact_match = AsyncMock(
            return_value=(None, None, None)
        )
        self.router_service.evaluate_semantic_cache = AsyncMock(
            return_value={"response": None, "latency_breakdown": {}}
        )
        self.router_service._background_save_cache = AsyncMock()

        async def fake_large_stream(*args, **kwargs):
            yield ChatCompletionChunk(
                id="c-large",
                created=1700000000,
                model="claude-3-5-sonnet-20241022",
                choices=[ChunkChoice(index=0, delta=ChunkDelta(role="assistant"))],
            )
            yield ChatCompletionChunk(
                id="c-large",
                created=1700000000,
                model="claude-3-5-sonnet-20241022",
                choices=[ChunkChoice(index=0, delta=ChunkDelta(content="X" * 100))],
            )
            yield ChatCompletionChunk(
                id="c-large",
                created=1700000000,
                model="claude-3-5-sonnet-20241022",
                choices=[
                    ChunkChoice(index=0, delta=ChunkDelta(), finish_reason="stop")
                ],
            )

        self.llm_provider_service.generate_stream = MagicMock(
            side_effect=fake_large_stream
        )

        req = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="Test overflow stream")],
        )

        with patch.object(config, "STREAM_MAX_CONTENT_BYTES", 50):
            chunks = []
            async for chunk in self.router_service.generate_stream(req):
                chunks.append(chunk)

            self.assertEqual(len(chunks), 3)
            self.assertEqual(chunks[1].choices[0].delta.content, "X" * 100)
            self.router_service._background_save_cache.assert_not_called()

    async def test_router_stream_pre_stream_fallback(self):
        self.router_service._get_redis_exact_match = AsyncMock(return_value=None)
        self.router_service._get_qdrant_exact_match = AsyncMock(
            return_value=(None, None, None)
        )
        self.router_service.evaluate_semantic_cache = AsyncMock(
            return_value={"response": None, "latency_breakdown": {}}
        )

        async def primary_fail_stream():
            raise HTTPException(status_code=503, detail="Primary down")
            yield

        async def fallback_ok_stream():
            yield ChatCompletionChunk(
                id="c-fallback",
                created=1700000000,
                model="gemini-3.1-flash-lite",
                choices=[ChunkChoice(index=0, delta=ChunkDelta(role="assistant"))],
            )
            yield ChatCompletionChunk(
                id="c-fallback",
                created=1700000000,
                model="gemini-3.1-flash-lite",
                choices=[
                    ChunkChoice(
                        index=0, delta=ChunkDelta(content="Hello from fallback")
                    )
                ],
            )

        def mock_generate_stream(req, service_name=None):
            if service_name == "claude":
                return primary_fail_stream()
            else:
                return fallback_ok_stream()

        self.llm_provider_service.generate_stream = MagicMock(
            side_effect=mock_generate_stream
        )

        req = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[ChatMessage(role="user", content="Test fallback stream")],
        )

        with (
            patch.object(config, "USE_FALLBACK_LLM", "true"),
            patch.object(config, "FALLBACK_LLM_SERVICE", "gemini"),
            patch.object(config, "FALLBACK_LLM_MODEL", "gemini-3.1-flash-lite"),
        ):
            chunks = []
            async for chunk in self.router_service.generate_stream(req):
                chunks.append(chunk)

            self.assertEqual(len(chunks), 2)
            self.assertEqual(chunks[1].choices[0].delta.content, "Hello from fallback")


class TestChatControllerStreamingPhase4(unittest.TestCase):
    def setUp(self):
        self.mock_router = MagicMock(spec=LlmRouterService)
        app.dependency_overrides[get_llm_router_service] = lambda: self.mock_router
        app.dependency_overrides[llm_quota_guard] = lambda: None
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_controller_non_streaming_json(self):
        mock_response = ChatCompletionResponse(
            id="chatcmpl-sync-1",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant", content="Synchronous JSON response"
                    ),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        self.mock_router.generate_completion = AsyncMock(return_value=mock_response)

        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Hello non-streaming"}],
            "stream": False,
        }

        resp = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("application/json"))
        data = resp.json()
        self.assertEqual(
            data["choices"][0]["message"]["content"], "Synchronous JSON response"
        )
        self.assertEqual(data["id"], "chatcmpl-sync-1")
        self.mock_router.generate_completion.assert_called_once()
        self.mock_router.generate_stream.assert_not_called()

    def test_controller_streaming_sse_success(self):
        async def fake_stream(*args, **kwargs):
            yield ChatCompletionChunk(
                id="c-stream-1",
                created=1700000000,
                model="gpt-4o-mini",
                choices=[ChunkChoice(index=0, delta=ChunkDelta(role="assistant"))],
            )
            yield ChatCompletionChunk(
                id="c-stream-1",
                created=1700000000,
                model="gpt-4o-mini",
                choices=[
                    ChunkChoice(index=0, delta=ChunkDelta(content="Streaming chunk 1 "))
                ],
            )
            yield ChatCompletionChunk(
                id="c-stream-1",
                created=1700000000,
                model="gpt-4o-mini",
                choices=[
                    ChunkChoice(index=0, delta=ChunkDelta(content="Streaming chunk 2"))
                ],
            )
            yield ChatCompletionChunk(
                id="c-stream-1",
                created=1700000000,
                model="gpt-4o-mini",
                choices=[
                    ChunkChoice(index=0, delta=ChunkDelta(), finish_reason="stop")
                ],
            )

        self.mock_router.generate_stream = MagicMock(side_effect=fake_stream)

        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Hello streaming"}],
            "stream": True,
        }

        resp = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(resp.headers["cache-control"], "no-cache")
        self.assertEqual(resp.headers["connection"], "keep-alive")

        # Parse SSE lines
        raw_text = resp.text
        lines = [line.strip() for line in raw_text.split("\n") if line.strip()]

        # Filter 'data: ...'
        data_lines = [
            d_line[len("data: ") :] for d_line in lines if d_line.startswith("data: ")
        ]
        self.assertGreaterEqual(len(data_lines), 4)

        # Last item must be [DONE]
        self.assertEqual(data_lines[-1], "[DONE]")

        # Parse intermediate chunks
        chunks = [json.loads(d) for d in data_lines[:-1]]
        text_parts = [
            c["choices"][0]["delta"]["content"]
            for c in chunks
            if "content" in c["choices"][0]["delta"]
        ]
        self.assertEqual("".join(text_parts), "Streaming chunk 1 Streaming chunk 2")

    def test_controller_pre_stream_error_returns_http_status(self):
        async def fail_pre_stream(*args, **kwargs):
            raise HTTPException(status_code=503, detail="Provider stream unavailable")
            yield  # pragma: no cover

        self.mock_router.generate_stream = MagicMock(side_effect=fail_pre_stream)

        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Will fail immediately"}],
            "stream": True,
        }

        resp = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "Provider stream unavailable")

    def test_controller_mid_stream_error_emits_sse_error_event(self):
        async def fail_mid_stream(*args, **kwargs):
            yield ChatCompletionChunk(
                id="c-err-1",
                created=1700000000,
                model="gpt-4o-mini",
                choices=[ChunkChoice(index=0, delta=ChunkDelta(role="assistant"))],
            )
            raise RuntimeError("Connection broken midway through stream")

        self.mock_router.generate_stream = MagicMock(side_effect=fail_mid_stream)

        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Will break mid-stream"}],
            "stream": True,
        }

        resp = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))

        raw_text = resp.text
        lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
        data_lines = [
            d_line[len("data: ") :] for d_line in lines if d_line.startswith("data: ")
        ]

        # Must end with [DONE]
        self.assertEqual(data_lines[-1], "[DONE]")

        # Second data payload is error payload
        error_json = json.loads(data_lines[1])
        self.assertIn("error", error_json)
        self.assertEqual(error_json["error"]["type"], "server_error")
        self.assertIn("Connection broken midway", error_json["error"]["message"])


class TestThinkingAndReasoningContent(unittest.IsolatedAsyncioTestCase):
    def test_accumulator_accumulates_reasoning_content(self):
        acc = StreamAccumulator(model="deepseek-r1")
        acc.add_chunk(
            ChatCompletionChunk(
                id="c-1",
                created=1700000000,
                model="deepseek-r1",
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(
                            role="assistant",
                            reasoning_content="Step 1: Understand problem.",
                        ),
                        finish_reason=None,
                    )
                ],
            )
        )
        acc.add_chunk(
            ChatCompletionChunk(
                id="c-1",
                created=1700000000,
                model="deepseek-r1",
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(
                            reasoning_content=" Step 2: Formulate solution."
                        ),
                        finish_reason=None,
                    )
                ],
            )
        )
        acc.add_chunk(
            ChatCompletionChunk(
                id="c-1",
                created=1700000000,
                model="deepseek-r1",
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(content="The final answer is 42."),
                        finish_reason="stop",
                    )
                ],
            )
        )

        resp = acc.build_response()
        self.assertEqual(resp.choices[0].message.content, "The final answer is 42.")
        self.assertEqual(
            resp.choices[0].message.reasoning_content,
            "Step 1: Understand problem. Step 2: Formulate solution.",
        )
        self.assertEqual(resp.choices[0].finish_reason, "stop")

    def test_response_to_stream_chunks_includes_reasoning(self):
        resp = ChatCompletionResponse(
            id="resp-reason-1",
            created=1700000000,
            model="o1-preview",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content="Here is the result.",
                        reasoning_content="Thinking carefully...",
                    ),
                    finish_reason="stop",
                )
            ],
        )

        chunks = response_to_stream_chunks(resp)
        # Should have role chunk, reasoning chunk, content chunk, finish chunk
        self.assertEqual(len(chunks), 4)
        self.assertEqual(chunks[0].choices[0].delta.role, "assistant")
        self.assertEqual(
            chunks[1].choices[0].delta.reasoning_content, "Thinking carefully..."
        )
        self.assertEqual(chunks[2].choices[0].delta.content, "Here is the result.")
        self.assertEqual(chunks[3].choices[0].finish_reason, "stop")

    async def test_mock_stream_emits_reasoning_when_thinking_enabled(self):
        service = LlmProviderService()
        req = ChatCompletionRequest(
            service_name="mock",
            model="gemini-3.8-flash",
            messages=[ChatMessage(role="user", content="Solve this logic puzzle")],
            thinking=True,
        )

        chunks = []
        async for chunk in service.generate_mock_stream(req):
            chunks.append(chunk)

        # First chunk is role
        self.assertEqual(chunks[0].choices[0].delta.role, "assistant")
        # Second chunk should be reasoning_content
        self.assertIsNotNone(chunks[1].choices[0].delta.reasoning_content)
        self.assertIn(
            "Analyzing the user request", chunks[1].choices[0].delta.reasoning_content
        )


if __name__ == "__main__":
    unittest.main()
