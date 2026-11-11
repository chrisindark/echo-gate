import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
from google.genai import errors, types

from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatMessage,
    FunctionCall,
    FunctionDefinition,
    Tool,
    ToolCall,
)
from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.llm.providers.gemini import (
    _THOUGHT_SIGNATURES,
    _store_thought_signature,
)


class TestGeminiProvider(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request = ChatCompletionRequest(
            service_name="google-genai",
            model="gemini-3.8-flash",
            messages=[ChatMessage(role="user", content="Hello")],
        )

    def test_store_thought_signature(self):
        _THOUGHT_SIGNATURES.clear()
        _store_thought_signature("", b"sig")
        assert len(_THOUGHT_SIGNATURES) == 0

        _store_thought_signature("call_1", b"sig1")
        assert _THOUGHT_SIGNATURES["call_1"] == b"sig1"

        # Test capacity eviction
        for i in range(2100):
            _store_thought_signature(f"call_{i}", f"sig_{i}".encode())
        assert len(_THOUGHT_SIGNATURES) < 2048

    def test_prepare_gemini_payload_complex(self):
        provider = LlmProviderService()
        tool = Tool(
            type="function",
            function=FunctionDefinition(
                name="get_weather",
                description="Get weather",
                parameters={
                    "type": "object",
                    "properties": {"loc": {"type": "string"}},
                },
            ),
        )

        _store_thought_signature("tc_1", b"thought_bytes")

        req = ChatCompletionRequest(
            service_name="google-genai",
            model="gemini-3.8-flash",
            messages=[
                ChatMessage(role="system", content="System instruction"),
                ChatMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc_1",
                            type="function",
                            function=FunctionCall(
                                name="get_weather", arguments='{"loc": "Paris"}'
                            ),
                        )
                    ],
                ),
                ChatMessage(role="tool", content='{"temp": 20}', tool_call_id="tc_1"),
                ChatMessage(role="user", content="What about Rome?"),
            ],
            temperature=0.7,
            top_p=0.9,
            max_tokens=100,
            tools=[tool],
            tool_choice="auto",
            response_format={"type": "json_object"},
            thinking=True,
            thinking_budget=1024,
        )

        contents, config = provider._prepare_gemini_payload(req)
        assert len(contents) == 3  # assistant, tool, user
        assert config.temperature == 0.7
        assert config.top_p == 0.9
        assert config.max_output_tokens == 100
        assert config.system_instruction == "System instruction"
        assert config.response_mime_type == "application/json"

    def test_prepare_gemini_tool_choice_variants(self):
        provider = LlmProviderService()

        # "none"
        req_none = ChatCompletionRequest(
            service_name="google-genai",
            model="gemini-3.8-flash",
            messages=[ChatMessage(role="user", content="hi")],
            tool_choice="none",
        )
        _, cfg_none = provider._prepare_gemini_payload(req_none)
        assert (
            cfg_none.tool_config.function_calling_config.mode
            == types.FunctionCallingConfigMode.NONE
        )

        # "required"
        req_req = ChatCompletionRequest(
            service_name="google-genai",
            model="gemini-3.8-flash",
            messages=[ChatMessage(role="user", content="hi")],
            tool_choice="required",
        )
        _, cfg_req = provider._prepare_gemini_payload(req_req)
        assert (
            cfg_req.tool_config.function_calling_config.mode
            == types.FunctionCallingConfigMode.ANY
        )

        # dict with function
        req_fn = ChatCompletionRequest(
            service_name="google-genai",
            model="gemini-3.8-flash",
            messages=[ChatMessage(role="user", content="hi")],
            tool_choice={"type": "function", "function": {"name": "lookup"}},
        )
        _, cfg_fn = provider._prepare_gemini_payload(req_fn)
        assert cfg_fn.tool_config.function_calling_config.allowed_function_names == [
            "lookup"
        ]

    def test_prepare_gemini_json_schema(self):
        provider = LlmProviderService()
        req_schema = ChatCompletionRequest(
            service_name="google-genai",
            model="gemini-3.8-flash",
            messages=[ChatMessage(role="user", content="hi")],
            response_format={
                "type": "json_schema",
                "json_schema": {"schema": {"type": "object"}},
            },
        )
        _, cfg = provider._prepare_gemini_payload(req_schema)
        assert cfg.response_mime_type == "application/json"
        assert cfg.response_json_schema == {"type": "object"}

    async def test_generate_gemini_completion_missing_key(self):
        provider = LlmProviderService()
        provider.gemini_client = None
        with self.assertRaises(HTTPException) as ctx:
            await provider.generate_gemini_completion(self.request)
        self.assertEqual(ctx.exception.status_code, 503)

    async def test_generate_gemini_completion_success(self):
        provider = LlmProviderService()
        mock_client = MagicMock()
        mock_client.aio = MagicMock()
        provider.gemini_client = mock_client

        # Mock response object
        mock_resp = MagicMock()
        mock_resp.text = "Hello from Gemini"
        mock_resp.usage_metadata = MagicMock(
            prompt_token_count=10, candidates_token_count=15, total_token_count=25
        )

        candidate = MagicMock()
        candidate.finish_reason = types.FinishReason.STOP
        part = MagicMock()
        part.thought = False
        part.text = "Hello from Gemini"
        part.function_call = None
        candidate.content = MagicMock(parts=[part])
        mock_resp.candidates = [candidate]
        mock_resp.function_calls = None

        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        res = await provider.generate_gemini_completion(self.request)
        self.assertEqual(res.choices[0].message.content, "Hello from Gemini")
        self.assertEqual(res.usage.prompt_tokens, 10)
        self.assertEqual(res.choices[0].finish_reason, "stop")

    async def test_generate_gemini_completion_function_calls(self):
        provider = LlmProviderService()
        mock_client = MagicMock()
        mock_client.aio = MagicMock()
        provider.gemini_client = mock_client

        mock_resp = MagicMock()
        mock_resp.text = None
        mock_resp.usage_metadata = None

        fc = MagicMock()
        fc.name = "get_weather"
        fc.args = {"loc": "London"}
        fc.id = "call_abc"

        candidate = MagicMock()
        candidate.finish_reason = None
        part = MagicMock()
        part.thought = False
        part.text = None
        part.function_call = fc
        part.thought_signature = b"sig_bytes"
        candidate.content = MagicMock(parts=[part])

        mock_resp.candidates = [candidate]
        mock_resp.function_calls = [fc]

        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        res = await provider.generate_gemini_completion(self.request)
        self.assertEqual(len(res.choices[0].message.tool_calls), 1)
        tc = res.choices[0].message.tool_calls[0]
        self.assertEqual(tc.function.name, "get_weather")
        self.assertEqual(tc.function.arguments, '{"loc": "London"}')
        self.assertEqual(res.choices[0].finish_reason, "tool_calls")

    async def test_generate_gemini_completion_errors(self):
        provider = LlmProviderService()
        mock_client = MagicMock()
        mock_client.aio = MagicMock()
        provider.gemini_client = mock_client

        # ClientError (400)
        client_err = errors.ClientError(400, "Bad Request")
        mock_client.aio.models.generate_content = AsyncMock(side_effect=client_err)
        with self.assertRaises(HTTPException) as ctx:
            await provider.generate_gemini_completion(self.request)
        self.assertEqual(ctx.exception.status_code, 400)

        # ServerError (500)
        server_err = errors.ServerError(500, "Gemini internal crash")
        mock_client.aio.models.generate_content = AsyncMock(side_effect=server_err)
        with self.assertRaises(HTTPException) as ctx:
            await provider.generate_gemini_completion(self.request)
        self.assertEqual(ctx.exception.status_code, 500)

        # Generic Exception -> 502
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError("unknown")
        )
        with self.assertRaises(HTTPException) as ctx:
            await provider.generate_gemini_completion(self.request)
        self.assertEqual(ctx.exception.status_code, 502)

    async def test_generate_gemini_stream(self):
        provider = LlmProviderService()
        mock_client = MagicMock()
        mock_client.aio = MagicMock()
        provider.gemini_client = mock_client

        # Missing key raises 503
        provider.gemini_client = None
        with self.assertRaises(HTTPException) as ctx:
            async for _ in provider.generate_gemini_stream(self.request):
                pass
        self.assertEqual(ctx.exception.status_code, 503)

        provider.gemini_client = mock_client

        # Mock stream chunks
        p1 = MagicMock()
        p1.thought = False
        p1.text = "Hello "
        p1.function_call = None
        chunk1 = MagicMock()
        chunk1.text = "Hello "
        chunk1.candidates = [
            MagicMock(finish_reason=None, content=MagicMock(parts=[p1]))
        ]
        chunk1.usage_metadata = None
        chunk1.function_calls = None

        p2 = MagicMock()
        p2.thought = False
        p2.text = "World"
        p2.function_call = None
        chunk2 = MagicMock()
        chunk2.text = "World"
        chunk2.candidates = [
            MagicMock(
                finish_reason=types.FinishReason.STOP, content=MagicMock(parts=[p2])
            )
        ]
        chunk2.usage_metadata = MagicMock(
            prompt_token_count=5, candidates_token_count=2, total_token_count=7
        )
        chunk2.function_calls = None

        async def fake_stream():
            yield chunk1
            yield chunk2

        mock_client.aio.models.generate_content_stream = AsyncMock(
            return_value=fake_stream()
        )

        chunks = [c async for c in provider.generate_gemini_stream(self.request)]
        self.assertGreaterEqual(len(chunks), 2)
        assert chunks[1].choices[0].delta.content == "Hello "

        # Error during stream
        async def fake_error_stream():
            yield chunk1
            raise errors.ClientError(400, "Stream interrupted")

        mock_client.aio.models.generate_content_stream = AsyncMock(
            return_value=fake_error_stream()
        )
        with self.assertRaises(HTTPException) as ctx:
            async for _ in provider.generate_gemini_stream(self.request):
                pass
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_prepare_gemini_payload_thinking_budget(self):
        provider = LlmProviderService()
        req = ChatCompletionRequest(
            service_name="gemini",
            model="gemini-3.8-flash",
            messages=[ChatMessage(role="user", content="Explain quantum physics")],
            thinking=True,
            thinking_budget=1024,
        )
        contents, cfg = provider._prepare_gemini_payload(req)
        self.assertIsNotNone(cfg.thinking_config)

    async def test_generate_gemini_stream_with_thinking_and_generic_error(self):
        provider = LlmProviderService()
        mock_client = MagicMock()
        mock_client.aio = MagicMock()
        provider.gemini_client = mock_client

        # Stream chunk with thought part
        p_thought = MagicMock()
        p_thought.thought = True
        p_thought.text = "I am pondering..."
        p_thought.function_call = None

        chunk_thought = MagicMock()
        chunk_thought.text = None
        chunk_thought.candidates = [
            MagicMock(finish_reason=None, content=MagicMock(parts=[p_thought]))
        ]
        chunk_thought.usage_metadata = None
        chunk_thought.function_calls = None

        async def fake_thought_stream():
            yield chunk_thought

        mock_client.aio.models.generate_content_stream = AsyncMock(
            return_value=fake_thought_stream()
        )
        chunks = [c async for c in provider.generate_gemini_stream(self.request)]
        # chunk 0 is assistant role, chunk 1 is reasoning_content
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual(
            chunks[1].choices[0].delta.reasoning_content, "I am pondering..."
        )

        # Generic stream error
        mock_client.aio.models.generate_content_stream = AsyncMock(
            side_effect=RuntimeError("Fatal socket error")
        )
        with self.assertRaises(HTTPException) as ctx:
            async for _ in provider.generate_gemini_stream(self.request):
                pass
        self.assertEqual(ctx.exception.status_code, 502)

    def test_prepare_gemini_payload_json_schema_variants(self):
        provider = LlmProviderService()

        # json_schema with nested schema dict
        req1 = ChatCompletionRequest(
            service_name="google-genai",
            model="gemini-3.8-flash",
            messages=[ChatMessage(role="user", content="schema test")],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "schema": {
                        "type": "object",
                        "properties": {"a": {"type": "string"}},
                    }
                },
            },
        )
        _, cfg1 = provider._prepare_gemini_payload(req1)
        self.assertEqual(cfg1.response_mime_type, "application/json")
        self.assertIn("properties", cfg1.response_json_schema)

        # json_schema with direct schema dict
        req2 = ChatCompletionRequest(
            service_name="google-genai",
            model="gemini-3.8-flash",
            messages=[ChatMessage(role="user", content="schema test 2")],
            response_format={
                "type": "json_schema",
                "schema": {"type": "object", "properties": {"b": {"type": "number"}}},
            },
        )
        _, cfg2 = provider._prepare_gemini_payload(req2)
        self.assertEqual(cfg2.response_mime_type, "application/json")
        self.assertIn("properties", cfg2.response_json_schema)

        # tool message with primitive string result
        req3 = ChatCompletionRequest(
            service_name="google-genai",
            model="gemini-3.8-flash",
            messages=[
                ChatMessage(
                    role="tool", content='"simple string"', tool_call_id="call_x"
                ),
                ChatMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="call_x",
                            type="function",
                            function=FunctionCall(name="fn_test", arguments="12345"),
                        )
                    ],
                ),
            ],
        )
        contents3, _ = provider._prepare_gemini_payload(req3)
        self.assertEqual(len(contents3), 2)

    async def test_generate_gemini_completion_thought_and_safety(self):
        provider = LlmProviderService()
        mock_client = MagicMock()
        mock_client.aio = MagicMock()
        provider.gemini_client = mock_client

        # Candidate with thought part and text part, plus thought_signature
        p_thought = MagicMock()
        p_thought.thought = True
        p_thought.text = "Deep thought"
        p_thought.function_call = None

        p_text = MagicMock()
        p_text.thought = False
        p_text.text = "Final answer"
        p_text.function_call = None

        mock_resp = MagicMock()
        mock_resp.text = "Final answer"
        mock_cand = MagicMock()
        mock_cand.content = MagicMock(parts=[p_thought, p_text])
        mock_cand.finish_reason = MagicMock(value="SAFETY")
        mock_resp.candidates = [mock_cand]
        mock_resp.function_calls = None
        mock_resp.usage_metadata = MagicMock(
            prompt_token_count=10, candidates_token_count=5, total_token_count=15
        )

        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_resp)
        resp = await provider.generate_gemini_completion(self.request)
        self.assertEqual(resp.choices[0].finish_reason, "stop")
        self.assertEqual(resp.choices[0].message.content, "Final answer")
        self.assertEqual(resp.choices[0].message.reasoning_content, "Deep thought")

        # Fallback to response.text when no candidate parts
        mock_resp2 = MagicMock()
        mock_resp2.candidates = []
        mock_resp2.function_calls = None
        mock_resp2.usage_metadata = None
        mock_resp2.text = "Direct text response"
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_resp2)
        resp2 = await provider.generate_gemini_completion(self.request)
        self.assertEqual(resp2.choices[0].message.content, "Direct text response")

    async def test_generate_gemini_stream_tool_calls_and_safety(self):
        provider = LlmProviderService()
        mock_client = MagicMock()
        mock_client.aio = MagicMock()
        provider.gemini_client = mock_client

        # Stream chunk with function_call and thought_signature
        fc = MagicMock(name="search_db", id="call_stream_1", args={"query": "test"})
        fc.name = "search_db"
        p_fc = MagicMock(
            function_call=fc,
            thought=False,
            text=None,
            thought_signature=b"sig_stream_bytes",
        )

        chunk_fc = MagicMock()
        chunk_fc.text = None
        cand_fc = MagicMock(finish_reason=None, content=MagicMock(parts=[p_fc]))
        chunk_fc.candidates = [cand_fc]
        chunk_fc.usage_metadata = None
        chunk_fc.function_calls = [fc]

        # Second chunk with safety finish reason
        cand_safety = MagicMock()
        cand_safety.finish_reason = MagicMock(value="SAFETY")
        cand_safety.content = None
        chunk_safety = MagicMock(
            text=None,
            candidates=[cand_safety],
            usage_metadata=None,
            function_calls=None,
        )

        async def fake_tool_stream():
            yield chunk_fc
            yield chunk_safety

        mock_client.aio.models.generate_content_stream = AsyncMock(
            return_value=fake_tool_stream()
        )
        chunks = [c async for c in provider.generate_gemini_stream(self.request)]
        self.assertGreaterEqual(len(chunks), 2)
        # Verify tool chunk delta has tool_calls
        tool_chunk = next(
            (
                c
                for c in chunks
                if c.choices and c.choices[0].delta and c.choices[0].delta.tool_calls
            ),
            None,
        )
        self.assertIsNotNone(tool_chunk)
        self.assertEqual(
            tool_chunk.choices[0].delta.tool_calls[0].function.name, "search_db"
        )
