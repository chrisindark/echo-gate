"""Unit tests for the Lossless Rule-Based Prompt Token Optimization Pipeline (Phase 5 / P3 Phase A)."""

import json
import unittest

from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatMessage,
    FunctionCall,
    ToolCall,
)
from app.modules.prompt_optimization.prompt_optimization_schema import (
    PromptOptimizationConfig,
)
from app.modules.prompt_optimization.prompt_optimizer import PromptOptimizer


class TestWhitespaceCompaction(unittest.TestCase):
    def setUp(self):
        self.optimizer = PromptOptimizer()

    def test_collapse_horizontal_and_vertical_whitespace(self):
        text = "Hello     world!   \n\n\n\nHere   is    another    line.  \t "
        collapsed = self.optimizer.collapse_whitespace(text)
        expected = "Hello world!\n\nHere is another line."
        self.assertEqual(collapsed, expected)

    def test_preserve_code_block_indentation(self):
        text = (
            "Here is some python code:    \n\n\n"
            "```python\n"
            "def calculate(x, y):   \n"
            "    # 4 spaces indentation\n"
            "    total = x + y\n"
            "\n\n\n"
            "    return total\n"
            "```\n\n\n"
            "Please   check   it.   "
        )
        collapsed = self.optimizer.collapse_whitespace(text)
        self.assertIn("def calculate(x, y):", collapsed)
        self.assertIn("    # 4 spaces indentation", collapsed)
        self.assertIn("    total = x + y", collapsed)
        self.assertIn("    return total", collapsed)
        self.assertNotIn("   \n", collapsed)
        self.assertNotIn("\n\n\n", collapsed)
        self.assertIn("Please check it.", collapsed)

    def test_inline_code_preserved(self):
        text = "Check `SELECT   *   FROM   table`   for details."
        collapsed = self.optimizer.collapse_whitespace(text)
        self.assertEqual(collapsed, "Check `SELECT   *   FROM   table` for details.")

    def test_empty_and_none_text(self):
        self.assertIsNone(self.optimizer.collapse_whitespace(None))
        self.assertEqual(self.optimizer.collapse_whitespace(""), "")
        self.assertEqual(self.optimizer.collapse_whitespace("   "), "")


class TestSessionDeduplication(unittest.TestCase):
    def setUp(self):
        self.optimizer = PromptOptimizer()

    def test_consecutive_identical_messages_deduplicated(self):
        messages = [
            ChatMessage(role="system", content="System instruction"),
            ChatMessage(role="user", content="hello"),
            ChatMessage(role="user", content="hello"),
            ChatMessage(role="assistant", content="Hi there!"),
            ChatMessage(role="user", content="What is the weather?"),
        ]
        deduped = self.optimizer.deduplicate_session(messages)
        self.assertEqual(len(deduped), 4)
        self.assertEqual(
            [m.content for m in deduped],
            ["System instruction", "hello", "Hi there!", "What is the weather?"],
        )

    def test_preserves_final_active_message(self):
        # Even if the final user message matches the prior user message, the active turn is kept
        messages = [
            ChatMessage(role="system", content="System prompt"),
            ChatMessage(role="user", content="status"),
            ChatMessage(role="assistant", content="ok"),
            ChatMessage(role="user", content="status"),
        ]
        deduped = self.optimizer.deduplicate_session(messages)
        self.assertEqual(len(deduped), 4)
        self.assertEqual(deduped[-1].content, "status")

    def test_deduplicates_repeated_turn_pairs(self):
        # User/Assistant pair repeated identically in history
        messages = [
            ChatMessage(role="system", content="System prompt"),
            ChatMessage(role="user", content="ping"),
            ChatMessage(role="assistant", content="pong"),
            ChatMessage(role="user", content="ping"),
            ChatMessage(role="assistant", content="pong"),
            ChatMessage(role="user", content="final question"),
        ]
        deduped = self.optimizer.deduplicate_session(messages)
        self.assertEqual(len(deduped), 4)
        self.assertEqual(
            [m.content for m in deduped],
            ["System prompt", "ping", "pong", "final question"],
        )

    def test_preserves_compound_tool_turns(self):
        # Assistant tool call and matching tool message must stay intact
        tool_call = ToolCall(
            id="call_123",
            function=FunctionCall(name="get_weather", arguments='{"city": "Paris"}'),
        )
        messages = [
            ChatMessage(role="system", content="System prompt"),
            ChatMessage(role="user", content="What's the weather in Paris?"),
            ChatMessage(role="assistant", content=None, tool_calls=[tool_call]),
            ChatMessage(role="tool", content='{"temp": 22}', tool_call_id="call_123"),
            ChatMessage(role="assistant", content="The temperature in Paris is 22C."),
            ChatMessage(role="user", content="Thanks!"),
        ]
        deduped = self.optimizer.deduplicate_session(messages)
        self.assertEqual(len(deduped), 6)
        # Verify tool call and tool message are preserved together
        self.assertEqual(deduped[2].tool_calls[0].id, "call_123")
        self.assertEqual(deduped[3].tool_call_id, "call_123")

    def test_short_sessions_unmodified(self):
        messages = [
            ChatMessage(role="user", content="hello"),
            ChatMessage(role="assistant", content="world"),
        ]
        deduped = self.optimizer.deduplicate_session(messages)
        self.assertEqual(len(deduped), 2)


class TestToolJsonTabularization(unittest.TestCase):
    def setUp(self):
        self.optimizer = PromptOptimizer()

    def test_tabularize_uniform_json_array(self):
        json_data = [
            {"id": 1, "name": "Alice", "role": "admin", "department": "Engineering"},
            {"id": 2, "name": "Bob", "role": "member", "department": "Engineering"},
            {"id": 3, "name": "Charlie", "role": "member", "department": "Design"},
        ]
        raw_json = json.dumps(json_data, indent=2)
        table = self.optimizer.tabularize_json_array(raw_json, min_rows=2)

        self.assertIn("| id | name | role | department |", table)
        self.assertIn("| --- | --- | --- | --- |", table)
        self.assertIn("| 1 | Alice | admin | Engineering |", table)
        self.assertIn("| 2 | Bob | member | Engineering |", table)
        self.assertIn("| 3 | Charlie | member | Design |", table)
        self.assertLess(len(table), len(raw_json))

    def test_tabularize_dict_with_items_array(self):
        payload = {
            "total": 2,
            "page": 1,
            "results": [
                {"sku": "A100", "price": 49.99, "stock": 10},
                {"sku": "B200", "price": 19.99, "stock": 50},
            ],
        }
        raw_json = json.dumps(payload, indent=2)
        table = self.optimizer.tabularize_json_array(raw_json, min_rows=2)

        self.assertIn("**results**:", table)
        self.assertIn("| sku | price | stock |", table)
        self.assertIn("| A100 | 49.99 | 10 |", table)
        self.assertIn("| B200 | 19.99 | 50 |", table)
        self.assertLess(len(table), len(raw_json))

    def test_cell_escaping_for_pipes_and_newlines(self):
        json_data = [
            {"id": 1, "notes": "Line 1\nLine 2", "tags": "a|b|c"},
            {"id": 2, "notes": "Single line", "tags": "x|y"},
        ]
        raw_json = json.dumps(json_data, indent=2)
        table = self.optimizer.tabularize_json_array(raw_json, min_rows=2)

        self.assertIn(r"a\|b\|c", table)
        self.assertIn("Line 1 Line 2", table)

    def test_non_uniform_or_scalar_json_not_converted(self):
        # Array of primitives should not be tabularized
        primitives = json.dumps([1, 2, 3, 4])
        self.assertEqual(self.optimizer.tabularize_json_array(primitives), primitives)

        # Non-JSON strings
        plain_text = "Database query returned 0 rows."
        self.assertEqual(self.optimizer.tabularize_json_array(plain_text), plain_text)

        # Single row when min_rows=2
        single_row = json.dumps([{"id": 1}])
        self.assertEqual(
            self.optimizer.tabularize_json_array(single_row, min_rows=2), single_row
        )


class TestConversationContextReduction(unittest.TestCase):
    def setUp(self):
        self.optimizer = PromptOptimizer()

    def test_ccr_condenses_intermediate_turns(self):
        messages = [
            ChatMessage(role="system", content="You are a helpful assistant."),
            # Intermediate turns
            ChatMessage(role="user", content="Turn 1: Tell me about astronomy."),
            ChatMessage(
                role="assistant", content="Astronomy is the study of celestial objects."
            ),
            ChatMessage(role="user", content="Turn 2: What is Mars?"),
            ChatMessage(
                role="assistant", content="Mars is the fourth planet from the Sun."
            ),
            ChatMessage(role="user", content="Turn 3: What about Jupiter?"),
            ChatMessage(
                role="assistant",
                content="Jupiter is the largest planet in our solar system.",
            ),
            # Recent turns (keep_last_n_turns=2)
            ChatMessage(role="user", content="Turn 4: Does Jupiter have rings?"),
            ChatMessage(role="assistant", content="Yes, Jupiter has faint rings."),
            ChatMessage(role="user", content="Turn 5: How many moons does it have?"),
        ]

        reduced = self.optimizer.reduce_context(
            messages,
            keep_last_n_turns=2,
            strategy="condense",
            max_turn_chars=50,
        )

        # Structure should be: System prompt + 1 condensed briefing turn + 2 recent turns
        self.assertEqual(reduced[0].role, "system")
        self.assertEqual(reduced[0].content, "You are a helpful assistant.")

        # Condensed briefing marker
        self.assertEqual(reduced[1].role, "system")
        self.assertIn("[Context briefing", reduced[1].content)
        self.assertIn("astronomy", reduced[1].content.lower())
        self.assertIn("mars", reduced[1].content.lower())

        # Last two turns preserved verbatim
        self.assertEqual(reduced[-2].content, "Yes, Jupiter has faint rings.")
        self.assertEqual(reduced[-1].content, "Turn 5: How many moons does it have?")

    def test_ccr_drops_intermediate_turns_with_marker(self):
        messages = [
            ChatMessage(role="system", content="System instructions."),
            ChatMessage(role="user", content="Question 1"),
            ChatMessage(role="assistant", content="Answer 1"),
            ChatMessage(role="user", content="Question 2"),
            ChatMessage(role="assistant", content="Answer 2"),
            ChatMessage(role="user", content="Question 3 (recent)"),
            ChatMessage(role="assistant", content="Answer 3 (recent)"),
            ChatMessage(role="user", content="Question 4 (active)"),
        ]

        reduced = self.optimizer.reduce_context(
            messages,
            keep_last_n_turns=2,
            strategy="drop",
        )

        self.assertEqual(reduced[0].content, "System instructions.")
        self.assertIn("omitted for context reduction", reduced[1].content)
        self.assertEqual(reduced[-2].content, "Answer 3 (recent)")
        self.assertEqual(reduced[-1].content, "Question 4 (active)")

    def test_ccr_unmodified_when_turns_below_threshold(self):
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Hi"),
            ChatMessage(role="assistant", content="Hello"),
            ChatMessage(role="user", content="Question"),
        ]
        reduced = self.optimizer.reduce_context(messages, keep_last_n_turns=5)
        self.assertEqual(len(reduced), len(messages))


class TestHeadroomTrimmer(unittest.TestCase):
    def setUp(self):
        self.optimizer = PromptOptimizer()

    def test_headroom_no_trim_when_within_budget(self):
        messages = [
            ChatMessage(role="system", content="System prompt"),
            ChatMessage(role="user", content="Short question"),
        ]
        trimmed = self.optimizer.trim_headroom(
            messages,
            model="gpt-4o-mini",
            target_tokens=500,
            buffer_tokens=50,
        )
        self.assertEqual(len(trimmed), 2)
        self.assertEqual(trimmed[0].content, "System prompt")
        self.assertEqual(trimmed[1].content, "Short question")

    def test_headroom_prunes_oldest_turns_when_exceeding_budget(self):
        # Create a conversation with multiple turns that exceeds a small target token budget
        messages = [
            ChatMessage(role="system", content="System instructions."),
            ChatMessage(role="user", content="Oldest question: " + "word " * 30),
            ChatMessage(role="assistant", content="Oldest answer: " + "word " * 30),
            ChatMessage(role="user", content="Middle question: " + "word " * 30),
            ChatMessage(role="assistant", content="Middle answer: " + "word " * 30),
            ChatMessage(role="user", content="Recent active prompt: " + "word " * 10),
        ]

        # Target 150 allowed tokens
        trimmed = self.optimizer.trim_headroom(
            messages,
            model="gpt-4o-mini",
            target_tokens=150,
            buffer_tokens=10,
        )

        # Verify system prompt and recent active prompt are preserved
        self.assertEqual(trimmed[0].content, "System instructions.")
        self.assertIn("Recent active prompt", trimmed[-1].content)
        # Oldest turns should have been pruned
        self.assertNotIn("Oldest question", [m.content for m in trimmed])
        self.assertLess(len(trimmed), len(messages))

    def test_headroom_truncates_large_active_prompt_when_alone_exceeds(self):
        messages = [
            ChatMessage(role="system", content="System instructions."),
            ChatMessage(
                role="user", content="A massive active prompt: " + "word " * 200
            ),
        ]

        trimmed = self.optimizer.trim_headroom(
            messages,
            model="gpt-4o-mini",
            target_tokens=80,
            buffer_tokens=10,
        )

        self.assertEqual(trimmed[0].content, "System instructions.")
        self.assertIn(
            "[... Content truncated to fit within model context window ...]",
            trimmed[1].content,
        )


class TestFullOptimizationPipeline(unittest.TestCase):
    def setUp(self):
        self.optimizer = PromptOptimizer()

    def test_optimize_applies_all_strategies(self):
        tool_content = json.dumps(
            [
                {"id": 101, "name": "Device A", "status": "active"},
                {"id": 102, "name": "Device B", "status": "offline"},
                {"id": 103, "name": "Device C", "status": "active"},
            ],
            indent=2,
        )
        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(
                    role="system",
                    content="You    are   a    helpful    assistant.   \n\n\n\n",
                ),
                ChatMessage(role="user", content="list   devices   "),
                ChatMessage(role="user", content="list   devices   "),
                ChatMessage(
                    role="tool", content=tool_content, tool_call_id="call_list"
                ),
                ChatMessage(
                    role="user", content="Which   devices   are   active?  \n\n"
                ),
            ],
            prompt_optimization=True,
        )

        optimized_req, result = self.optimizer.optimize(request)
        self.assertTrue(result.applied)
        self.assertIn("whitespace", result.strategies)
        self.assertIn("session-dedup", result.strategies)
        self.assertIn("json-table", result.strategies)
        self.assertGreater(result.chars_saved, 0)
        self.assertGreater(result.tokens_saved, 0)

        # Verify system prompt whitespace collapsed
        self.assertEqual(
            optimized_req.messages[0].content, "You are a helpful assistant."
        )
        # Verify user duplicate turn removed
        self.assertEqual(len(optimized_req.messages), 4)
        # Verify tool message tabularized
        self.assertIn("| id | name | status |", optimized_req.messages[2].content)

    def test_optimize_respects_opt_out(self):
        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(role="user", content="Hello      world!   \n\n\n"),
            ],
            prompt_optimization=False,
        )
        optimized_req, result = self.optimizer.optimize(request)
        self.assertFalse(result.applied)
        self.assertEqual(
            optimized_req.messages[0].content, "Hello      world!   \n\n\n"
        )

    def test_selective_strategies(self):
        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(role="user", content="foo   "),
                ChatMessage(role="user", content="foo   "),
                ChatMessage(role="user", content="bar   "),
            ],
            prompt_optimization=PromptOptimizationConfig(
                enabled=True,
                whitespace=False,
                session_dedup=True,
                json_table=False,
            ),
        )
        optimized_req, result = self.optimizer.optimize(request)
        self.assertTrue(result.applied)
        self.assertEqual(result.strategies, ["session-dedup"])
        # Duplicate consecutive 'foo   ' removed, but whitespace inside 'foo   ' preserved
        self.assertEqual(len(optimized_req.messages), 2)
        self.assertEqual(optimized_req.messages[0].content, "foo   ")

    def test_optimize_with_ccr_and_headroom_trimming(self):
        messages = [
            ChatMessage(role="system", content="System instruction.   \n\n"),
            ChatMessage(
                role="user",
                content="Turn 1: question A about quantum computing mechanics and qubits",
            ),
            ChatMessage(
                role="assistant",
                content="Turn 1: answer A detailing superposition, entanglement, and decoherence times",
            ),
            ChatMessage(
                role="user",
                content="Turn 2: question B about error correction surface codes",
            ),
            ChatMessage(
                role="assistant",
                content="Turn 2: answer B detailing topological surface codes and physical-to-logical qubit overheads",
            ),
            ChatMessage(
                role="user",
                content="Turn 3: question C about cryogenic dilution refrigerators",
            ),
            ChatMessage(
                role="assistant",
                content="Turn 3: answer C describing millikelvin cooling with helium-3 and helium-4 isotopes",
            ),
            ChatMessage(
                role="user",
                content="Turn 4: question D about commercial cloud hardware",
            ),
            ChatMessage(
                role="assistant",
                content="Turn 4: answer D listing current superconducting quantum hardware available via cloud APIs",
            ),
            ChatMessage(role="user", content="Turn 5: active prompt"),
        ]
        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=messages,
            prompt_optimization=PromptOptimizationConfig(
                enabled=True,
                whitespace=True,
                session_dedup=True,
                json_table=True,
                ccr=True,
                ccr_keep_last_n_turns=2,
                ccr_max_turn_chars=40,
                headroom_trim=True,
                headroom_target_tokens=300,
            ),
        )
        optimized_req, result = self.optimizer.optimize(request)
        self.assertTrue(result.applied)
        self.assertIn("ccr", result.strategies)
        # Verify system prompt kept
        self.assertEqual(optimized_req.messages[0].role, "system")
        self.assertEqual(optimized_req.messages[0].content, "System instruction.")
        # Verify condensed briefing present
        self.assertIn("[Context briefing", optimized_req.messages[1].content)
        # Verify active prompt kept
        self.assertEqual(optimized_req.messages[-1].content, "Turn 5: active prompt")

    def test_ccr_with_tool_calls_in_intermediate_turns(self):
        messages = [
            ChatMessage(role="system", content="System instruction."),
            ChatMessage(role="user", content="Turn 1: fetch data"),
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        type="function",
                        function=FunctionCall(name="query_database", arguments="{}"),
                    )
                ],
            ),
            ChatMessage(role="tool", tool_call_id="call_1", content="ok"),
            ChatMessage(role="user", content="Turn 2: analyze"),
            ChatMessage(role="assistant", content="Turn 2 answer"),
            ChatMessage(role="user", content="Turn 3: final question"),
        ]
        reduced = self.optimizer.reduce_context(
            messages, keep_last_n_turns=1, strategy="condense"
        )
        briefing = reduced[1].content
        self.assertIn("Called tool(s): query_database", briefing)

    def test_headroom_trim_model_registry_fallback(self):
        # target_tokens=None, uses model_registry lookup
        long_content = "Word " * 200
        messages = [
            ChatMessage(role="system", content="System instructions"),
            ChatMessage(role="user", content=long_content),
            ChatMessage(role="assistant", content=long_content),
            ChatMessage(role="user", content="Active query"),
        ]
        # mock model has default context window, buffer_tokens trimmed
        trimmed = self.optimizer.trim_headroom(
            messages,
            model="gpt-4o-mini",
            target_tokens=300,
            buffer_tokens=50,
        )
        self.assertLess(len(trimmed), len(messages))

    def test_optimize_no_op_when_already_minimal(self):
        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Minimal content")],
            prompt_optimization=PromptOptimizationConfig(
                enabled=True,
                whitespace=True,
                session_dedup=True,
                json_table=True,
                ccr=False,
                headroom_trim=False,
            ),
        )
        optimized_req, result = self.optimizer.optimize(request)
        self.assertFalse(result.applied)
        self.assertEqual(result.chars_saved, 0)
        self.assertEqual(result.tokens_saved, 0)

    def test_optimize_fail_open_on_exception(self):
        from unittest.mock import patch

        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Hello")],
            prompt_optimization=PromptOptimizationConfig(enabled=True),
        )
        with patch.object(
            self.optimizer,
            "deduplicate_session",
            side_effect=RuntimeError("Unexpected error"),
        ):
            optimized_req, result = self.optimizer.optimize(request)
            self.assertFalse(result.applied)
            self.assertEqual(optimized_req, request)

    def test_compress_sync_applies_lossy_compression(self):
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_client.compress_sync.return_value = [
            {
                "index": 0,
                "original_text": "This is a very long and detailed explanation that should be compressed.",
                "compressed_text": "detailed explanation compressed.",
                "original_tokens": 12,
                "compressed_tokens": 3,
            }
        ]
        optimizer = PromptOptimizer(inference_client=mock_client)
        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(role="system", content="System instruction."),
                ChatMessage(
                    role="assistant",
                    content="This is a very long and detailed explanation that should be compressed.",
                ),
                ChatMessage(role="user", content="Next question."),
            ],
            prompt_optimization=PromptOptimizationConfig(
                enabled=True,
                whitespace=False,
                session_dedup=False,
                json_table=False,
                ccr=False,
                headroom_trim=False,
                compress=True,
                compress_rate=0.5,
            ),
        )
        optimized_req, result = optimizer.optimize(request)
        self.assertTrue(result.applied)
        self.assertIn("compress", result.strategies)
        self.assertEqual(
            optimized_req.messages[1].content, "detailed explanation compressed."
        )

    def test_compress_async_applies_lossy_compression(self):
        import asyncio
        from unittest.mock import AsyncMock

        mock_client = AsyncMock()
        mock_client.compress.return_value = [
            {
                "index": 0,
                "original_text": "This is a very long and detailed explanation that should be compressed.",
                "compressed_text": "detailed explanation compressed.",
                "original_tokens": 12,
                "compressed_tokens": 3,
            }
        ]
        optimizer = PromptOptimizer(inference_client=mock_client)
        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(role="system", content="System instruction."),
                ChatMessage(
                    role="assistant",
                    content="This is a very long and detailed explanation that should be compressed.",
                ),
                ChatMessage(role="user", content="Next question."),
            ],
            prompt_optimization=PromptOptimizationConfig(
                enabled=True,
                whitespace=False,
                session_dedup=False,
                json_table=False,
                ccr=False,
                headroom_trim=False,
                compress=True,
                compress_rate=0.5,
            ),
        )
        optimized_req, result = asyncio.run(optimizer.optimize_async(request))
        self.assertTrue(result.applied)
        self.assertIn("compress", result.strategies)
        self.assertEqual(
            optimized_req.messages[1].content, "detailed explanation compressed."
        )

    def test_compress_fail_open_on_client_error(self):
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_client.compress_sync.side_effect = RuntimeError("Inference down")
        optimizer = PromptOptimizer(inference_client=mock_client)
        long_content = (
            "This is a very long and detailed explanation that should be compressed."
        )
        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(role="assistant", content=long_content),
            ],
            prompt_optimization=PromptOptimizationConfig(
                enabled=True,
                whitespace=False,
                session_dedup=False,
                json_table=False,
                ccr=False,
                headroom_trim=False,
                compress=True,
            ),
        )
        optimized_req, result = optimizer.optimize(request)
        self.assertFalse(result.applied)
        self.assertEqual(optimized_req.messages[0].content, long_content)

    def test_optimize_json_table_and_session_dedup_integration(self):
        import asyncio

        optimizer = PromptOptimizer()
        tool_json = json.dumps(
            [
                {"id": 1, "name": "Alpha", "status": "active"},
                {"id": 2, "name": "Beta", "status": "inactive"},
                {"id": 3, "name": "Gamma", "status": "pending"},
            ],
            indent=2,
        )

        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(role="system", content="System instruction"),
                ChatMessage(role="user", content="hello"),
                ChatMessage(role="user", content="hello"),
                ChatMessage(role="tool", content=tool_json, tool_call_id="call_1"),
                ChatMessage(role="user", content="Next task"),
            ],
            prompt_optimization=PromptOptimizationConfig(
                enabled=True,
                whitespace=True,
                session_dedup=True,
                json_table=True,
                ccr=False,
                headroom_trim=False,
                compress=False,
            ),
        )
        opt_req, result = asyncio.run(optimizer.optimize_async(request))
        self.assertTrue(result.applied)
        self.assertIn("json-table", result.strategies)
        self.assertIn("session-dedup", result.strategies)

    def test_optimize_async_config_variants(self):
        import asyncio

        optimizer = PromptOptimizer()
        req_dict = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello  world")],
            prompt_optimization={"enabled": True, "whitespace": True},
        )
        opt_req, res = asyncio.run(optimizer.optimize_async(req_dict))
        self.assertTrue(res.applied)

        req_true = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello  world")],
            prompt_optimization=True,
        )
        opt_req, res = asyncio.run(optimizer.optimize_async(req_true))
        self.assertTrue(res.applied)

        req_false = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello  world")],
            prompt_optimization=False,
        )
        opt_req, res = asyncio.run(optimizer.optimize_async(req_false))
        self.assertFalse(res.applied)

        from unittest.mock import patch

        from app.core.config import config

        with (
            patch.object(config, "PROMPT_OPTIMIZATION_ENABLED", True),
            patch.object(config, "PROMPT_OPTIMIZATION_WHITESPACE", True),
        ):
            req_none = ChatCompletionRequest(
                service_name="openai",
                model="gpt-4o-mini",
                messages=[ChatMessage(role="user", content="hello  world")],
                prompt_optimization=None,
            )
            opt_req, res = asyncio.run(optimizer.optimize_async(req_none))
            self.assertTrue(res.applied)

        # with options parameter
        opt_req, res = asyncio.run(
            optimizer.optimize_async(
                req_false,
                options=PromptOptimizationConfig(enabled=True, whitespace=True),
            )
        )
        self.assertTrue(res.applied)

    def test_optimize_fail_open_on_generic_exception(self):
        import asyncio

        optimizer = PromptOptimizer()
        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hello")],
            prompt_optimization=PromptOptimizationConfig(enabled=True),
        )
        from unittest.mock import patch

        with patch.object(
            optimizer,
            "collapse_whitespace",
            side_effect=RuntimeError("unexpected crash"),
        ):
            opt_req, result = optimizer.optimize(request)
            self.assertFalse(result.applied)
            self.assertEqual(opt_req, request)

            opt_req_async, result_async = asyncio.run(optimizer.optimize_async(request))
            self.assertFalse(result_async.applied)
            self.assertEqual(opt_req_async, request)

    def test_optimize_no_savings_returns_applied_false(self):
        import asyncio

        optimizer = PromptOptimizer()
        # Message already minimal with whitespace
        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="word")],
            prompt_optimization=PromptOptimizationConfig(
                enabled=True,
                whitespace=True,
                session_dedup=False,
                json_table=False,
                ccr=False,
                headroom_trim=False,
                compress=False,
            ),
        )
        opt_req, result = optimizer.optimize(request)
        self.assertFalse(result.applied)
        self.assertEqual(result.chars_saved, 0)

        opt_req_async, result_async = asyncio.run(optimizer.optimize_async(request))
        self.assertFalse(result_async.applied)
        self.assertEqual(result_async.chars_saved, 0)


if __name__ == "__main__":
    unittest.main()
