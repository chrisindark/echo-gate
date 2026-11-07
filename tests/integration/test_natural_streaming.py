import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.config import config
from app.core.constants import EchoGateHeaders
from app.core.dependencies import get_llm_router_service
from app.core.guards import llm_quota_guard
from app.main import app
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    Usage,
    apply_request_directives,
    response_to_stream_chunks,
    response_to_stream_chunks_natural,
    split_content_into_natural_chunks,
)
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.prompt_optimization.prompt_optimizer import PromptOptimizer


class TestNaturalStreaming(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved_cache_mode = config.CACHE_MODE
        config.CACHE_MODE = "normal"

        self.router = LlmRouterService(
            llm_provider_service=MagicMock(),
            embedding_service=MagicMock(),
            llm_cache_service=MagicMock(),
            redis_service=MagicMock(),
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=MagicMock(),
            prompt_optimizer=PromptOptimizer(),
        )
        self.router._record_cache_hit = MagicMock()

        def close_coro(coro):
            if hasattr(coro, "close"):
                coro.close()
            return MagicMock()

        self.router._create_background_task = MagicMock(side_effect=close_coro)

        async def dummy_quota():
            return None

        app.dependency_overrides[get_llm_router_service] = lambda: self.router
        app.dependency_overrides[llm_quota_guard] = dummy_quota
        self.client = TestClient(app)

    def tearDown(self):
        config.CACHE_MODE = self._saved_cache_mode
        app.dependency_overrides.clear()

    def test_split_content_into_natural_chunks_basic(self):
        text = "The quick brown fox jumps over the lazy dog."
        chunks = split_content_into_natural_chunks(text, words_per_chunk=3)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), text)

    def test_split_content_into_natural_chunks_preserves_code_and_formatting(self):
        code_text = (
            "Here is the implementation:\n\n"
            "```python\n"
            "def add(a: int, b: int) -> int:\n"
            "    # Add two numbers\n"
            "    return a + b\n"
            "```\n\n"
            "Enjoy using it!"
        )
        chunks = split_content_into_natural_chunks(code_text, words_per_chunk=3)
        self.assertEqual("".join(chunks), code_text)

    def test_split_content_into_natural_chunks_edge_cases(self):
        # Empty
        self.assertEqual(split_content_into_natural_chunks(""), [])
        # Whitespace
        self.assertEqual(split_content_into_natural_chunks("   \n\t "), ["   \n\t "])
        # Unbroken word
        long_word = "a" * 80
        chunks = split_content_into_natural_chunks(
            long_word, words_per_chunk=3, max_chars_per_chunk=16
        )
        self.assertEqual("".join(chunks), long_word)
        self.assertGreater(len(chunks), 1)
        # CJK text
        cjk_text = "你好世界，这是一个自然的流式缓存播放测试！"
        cjk_chunks = split_content_into_natural_chunks(cjk_text, words_per_chunk=3)
        self.assertEqual("".join(cjk_chunks), cjk_text)
        self.assertGreater(len(cjk_chunks), 1)

    def test_response_to_stream_chunks_natural(self):
        long_content = (
            "Echo Gate provides resilient AI gateway infrastructure with fail-open caching "
            "and multi-provider routing support for enterprise applications."
        )
        resp = ChatCompletionResponse(
            id="chatcmpl-test-natural",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content=long_content,
                        reasoning_content="Let me reason through this gateway design first.",
                    ),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=15, completion_tokens=25, total_tokens=40),
            cache_info={"source": "redis", "cache_hit": True},
        )
        resp._degradation_state = "none"

        # Standard conversion (monolithic)
        std_chunks = response_to_stream_chunks(resp, natural=False)
        content_std = [
            c for c in std_chunks if c.choices and c.choices[0].delta.content
        ]
        self.assertEqual(len(content_std), 1)
        self.assertEqual(content_std[0].choices[0].delta.content, long_content)

        # Natural conversion (token/word sliced)
        nat_chunks = response_to_stream_chunks_natural(resp, words_per_chunk=3)
        content_nat = [
            c for c in nat_chunks if c.choices and c.choices[0].delta.content
        ]
        reasoning_nat = [
            c for c in nat_chunks if c.choices and c.choices[0].delta.reasoning_content
        ]

        self.assertGreater(len(content_nat), 1)
        self.assertGreater(len(reasoning_nat), 1)
        self.assertEqual(
            "".join(c.choices[0].delta.content for c in content_nat), long_content
        )
        self.assertEqual(
            "".join(c.choices[0].delta.reasoning_content for c in reasoning_nat),
            "Let me reason through this gateway design first.",
        )

        # First chunk is role
        self.assertEqual(nat_chunks[0].choices[0].delta.role, "assistant")
        self.assertEqual(nat_chunks[0]._degradation_state, "none")
        # Last chunk has finish reason and usage
        self.assertEqual(nat_chunks[-1].choices[0].finish_reason, "stop")
        self.assertIsNotNone(nat_chunks[-1].usage)
        self.assertEqual(nat_chunks[-1].usage.total_tokens, 40)

    def test_apply_request_directives_stream_pacing(self):
        # 1. Pacing off via header
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        apply_request_directives(req, {EchoGateHeaders.STREAM_PACING: "off"})
        self.assertIs(req.stream_pacing, False)
        self.assertEqual(req.stream_pacing_delay_ms, 0.0)

        # 2. Custom delay via header with ms
        req2 = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        apply_request_directives(req2, {EchoGateHeaders.STREAM_PACING: "15ms"})
        self.assertIs(req2.stream_pacing, True)
        self.assertEqual(req2.stream_pacing_delay_ms, 15.0)

        # 3. Numeric delay via header
        req3 = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        apply_request_directives(req3, {EchoGateHeaders.STREAM_PACING: "25"})
        self.assertIs(req3.stream_pacing, True)
        self.assertEqual(req3.stream_pacing_delay_ms, 25.0)

        # 4. Explicit stream_pacing False in request body zeroes delay
        req4 = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Hi")],
            stream_pacing=False,
        )
        apply_request_directives(req4, {})
        self.assertIs(req4.stream_pacing, False)
        self.assertEqual(req4.stream_pacing_delay_ms, 0.0)

    async def test_router_generate_stream_pacing_cache_hit(self):
        long_content = "One two three four five six seven eight nine ten eleven twelve."
        cached_resp = ChatCompletionResponse(
            id="chatcmpl-cached-pacing",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=long_content),
                    finish_reason="stop",
                )
            ],
            cache_info={"source": "redis", "cache_hit": True},
        )
        self.router._lookup_serving_cache = AsyncMock(return_value=(cached_resp, {}))

        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Test natural pacing")],
            stream=True,
            stream_pacing=True,
            stream_pacing_delay_ms=10.0,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            chunks = []
            async for chunk in self.router.generate_stream(req):
                chunks.append(chunk)

            self.assertGreaterEqual(len(chunks), 4)
            # Role chunk + multiple content chunks + finish chunk
            content_chunks = [
                c for c in chunks if c.choices and c.choices[0].delta.content
            ]
            self.assertGreater(len(content_chunks), 1)
            self.assertEqual(
                "".join(c.choices[0].delta.content for c in content_chunks),
                long_content,
            )

            # mock_sleep should have been called between content chunks with 0.01s (10ms)
            self.assertGreaterEqual(mock_sleep.call_count, len(content_chunks))
            for call in mock_sleep.call_args_list:
                self.assertAlmostEqual(call.args[0], 0.010, places=3)

    async def test_router_generate_stream_pacing_disabled(self):
        long_content = "One two three four five six seven eight nine ten."
        cached_resp = ChatCompletionResponse(
            id="chatcmpl-cached-nopacing",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=long_content),
                    finish_reason="stop",
                )
            ],
            cache_info={"source": "redis", "cache_hit": True},
        )
        self.router._lookup_serving_cache = AsyncMock(return_value=(cached_resp, {}))

        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Test no pacing")],
            stream=True,
            stream_pacing=False,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            chunks = []
            async for chunk in self.router.generate_stream(req):
                chunks.append(chunk)

            self.assertGreaterEqual(len(chunks), 4)
            mock_sleep.assert_not_called()

    def test_controller_chat_completions_natural_streaming_cache_hit(self):
        cached_text = "Echo Gate provides natural streaming playback for cached responses smoothly."
        cached_resp = ChatCompletionResponse(
            id="chatcmpl-cached-controller",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=cached_text),
                    finish_reason="stop",
                )
            ],
            cache_info={"source": "redis", "cache_hit": True},
        )
        self.router._lookup_serving_cache = AsyncMock(return_value=(cached_resp, {}))

        payload = {
            "service_name": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Tell me about Echo Gate"}],
            "stream": True,
            "stream_pacing": False,  # fast test without network sleeps
        }

        resp = self.client.post("/api/v1/chat/completions", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(resp.headers.get(EchoGateHeaders.CACHE), "HIT")

        raw_text = resp.text
        lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
        data_lines = [
            line[len("data: ") :] for line in lines if line.startswith("data: ")
        ]

        self.assertGreaterEqual(len(data_lines), 4)
        self.assertEqual(data_lines[-1], "[DONE]")

        chunks = [json.loads(d) for d in data_lines[:-1]]
        text_parts = [
            c["choices"][0]["delta"]["content"]
            for c in chunks
            if "content" in c["choices"][0]["delta"]
            and c["choices"][0]["delta"]["content"]
        ]
        self.assertGreater(len(text_parts), 1)
        self.assertEqual("".join(text_parts), cached_text)

    def test_controller_anthropic_natural_streaming_cache_hit(self):
        cached_text = "Claude messages are delivered with natural word chunking through Echo Gate."
        cached_resp = ChatCompletionResponse(
            id="chatcmpl-cached-anthropic",
            created=1700000000,
            model="claude-3-5-sonnet-20241022",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=cached_text),
                    finish_reason="stop",
                )
            ],
            cache_info={"source": "redis", "cache_hit": True},
        )
        self.router._lookup_serving_cache = AsyncMock(return_value=(cached_resp, {}))

        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Hello Anthropic"}],
            "max_tokens": 100,
            "stream": True,
            "stream_pacing": False,
        }

        resp = self.client.post("/api/v1/messages", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(resp.headers.get("anthropic-version"), "2023-06-01")

        raw_text = resp.text
        # Check that multiple text_delta events exist and reassemble into the original cached text
        events = raw_text.strip().split("\n\n")
        deltas = []
        for ev in events:
            lines = ev.split("\n")
            event_type = None
            data = None
            for item in lines:
                if item.startswith("event: "):
                    event_type = item[len("event: ") :]
                elif item.startswith("data: "):
                    data = json.loads(item[len("data: ") :])
            if event_type == "content_block_delta" and data:
                delta_text = data.get("delta", {}).get("text", "")
                if delta_text:
                    deltas.append(delta_text)

        self.assertGreater(len(deltas), 1)
        self.assertEqual("".join(deltas), cached_text)


if __name__ == "__main__":
    unittest.main()
