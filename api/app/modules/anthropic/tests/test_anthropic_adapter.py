"""Unit tests for the Anthropic Messages Protocol Adapter."""

import json
import unittest

from app.modules.anthropic.anthropic_adapter import (
    normalize_anthropic_to_chat_request,
    normalize_chat_to_anthropic_response,
    transform_chat_stream_to_anthropic_sse,
)
from app.modules.anthropic.anthropic_schema import (
    AnthropicMessage,
    AnthropicMessagesRequest,
    AnthropicTool,
)
from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChunkChoice,
    ChunkDelta,
    FunctionCall,
    ToolCall,
    Usage,
)


class TestAnthropicAdapter(unittest.IsolatedAsyncioTestCase):
    def test_normalize_request_simple_text(self):
        req = AnthropicMessagesRequest(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            system="You are a helpful assistant.",
            messages=[
                AnthropicMessage(role="user", content="Hello Claude"),
            ],
            temperature=0.7,
            top_p=0.9,
        )
        chat_req = normalize_anthropic_to_chat_request(req)
        self.assertEqual(chat_req.model, "claude-3-5-sonnet-20241022")
        self.assertEqual(len(chat_req.messages), 2)
        self.assertEqual(chat_req.messages[0].role, "system")
        self.assertEqual(chat_req.messages[0].content, "You are a helpful assistant.")
        self.assertEqual(chat_req.messages[1].role, "user")
        self.assertEqual(chat_req.messages[1].content, "Hello Claude")
        self.assertEqual(chat_req.max_tokens, 1000)
        self.assertEqual(chat_req.temperature, 0.7)
        self.assertEqual(chat_req.top_p, 0.9)

    def test_normalize_request_with_blocks_and_tools(self):
        req = AnthropicMessagesRequest(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            system=[
                {"type": "text", "text": "Part 1."},
                {"type": "text", "text": "Part 2."},
            ],
            messages=[
                AnthropicMessage(
                    role="user",
                    content=[{"type": "text", "text": "What is the weather in SF?"}],
                ),
                AnthropicMessage(
                    role="assistant",
                    content=[
                        {
                            "type": "tool_use",
                            "id": "toolu_123",
                            "name": "get_weather",
                            "input": {"city": "San Francisco"},
                        }
                    ],
                ),
                AnthropicMessage(
                    role="user",
                    content=[
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_123",
                            "content": "Sunny, 68F",
                        }
                    ],
                ),
            ],
            tools=[
                AnthropicTool(
                    name="get_weather",
                    description="Get weather for a city",
                    input_schema={
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                )
            ],
        )
        chat_req = normalize_anthropic_to_chat_request(req)
        self.assertEqual(chat_req.messages[0].role, "system")
        self.assertEqual(chat_req.messages[0].content, "Part 1.\n\nPart 2.")

        # Assistant turn should have tool_calls
        assistant_msg = chat_req.messages[2]
        self.assertEqual(assistant_msg.role, "assistant")
        self.assertIsNotNone(assistant_msg.tool_calls)
        self.assertEqual(len(assistant_msg.tool_calls), 1)
        self.assertEqual(assistant_msg.tool_calls[0].id, "toolu_123")
        self.assertEqual(assistant_msg.tool_calls[0].function.name, "get_weather")
        self.assertEqual(
            json.loads(assistant_msg.tool_calls[0].function.arguments),
            {"city": "San Francisco"},
        )

        # Tool result turn
        tool_msg = chat_req.messages[3]
        self.assertEqual(tool_msg.role, "tool")
        self.assertEqual(tool_msg.tool_call_id, "toolu_123")
        self.assertEqual(tool_msg.content, "Sunny, 68F")

        # Tools
        self.assertIsNotNone(chat_req.tools)
        self.assertEqual(len(chat_req.tools), 1)
        self.assertEqual(chat_req.tools[0].function.name, "get_weather")

    def test_normalize_chat_to_anthropic_response(self):
        chat_resp = ChatCompletionResponse(
            id="chatcmpl-test-xyz",
            object="chat.completion",
            created=1677652288,
            model="claude-3-5-sonnet-20241022",
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content="Here is your answer.",
                    ),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=15, completion_tokens=25, total_tokens=40),
        )
        anthropic_resp = normalize_chat_to_anthropic_response(chat_resp)
        self.assertEqual(anthropic_resp.id, "msg_test-xyz")
        self.assertEqual(anthropic_resp.type, "message")
        self.assertEqual(anthropic_resp.role, "assistant")
        self.assertEqual(anthropic_resp.model, "claude-3-5-sonnet-20241022")
        self.assertEqual(len(anthropic_resp.content), 1)
        self.assertEqual(anthropic_resp.content[0]["type"], "text")
        self.assertEqual(anthropic_resp.content[0]["text"], "Here is your answer.")
        self.assertEqual(anthropic_resp.stop_reason, "end_turn")
        self.assertEqual(anthropic_resp.usage.input_tokens, 15)
        self.assertEqual(anthropic_resp.usage.output_tokens, 25)

    def test_normalize_chat_to_anthropic_response_with_tool_call(self):
        chat_resp = ChatCompletionResponse(
            id="msg_existing-id",
            object="chat.completion",
            created=1677652288,
            model="claude-3-5-sonnet-20241022",
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
                                    name="calculate", arguments='{"x": 10, "y": 20}'
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=Usage(prompt_tokens=20, completion_tokens=30, total_tokens=50),
        )
        anthropic_resp = normalize_chat_to_anthropic_response(chat_resp)
        self.assertEqual(anthropic_resp.stop_reason, "tool_use")
        self.assertEqual(len(anthropic_resp.content), 1)
        self.assertEqual(anthropic_resp.content[0]["type"], "tool_use")
        self.assertEqual(anthropic_resp.content[0]["name"], "calculate")
        self.assertEqual(anthropic_resp.content[0]["input"], {"x": 10, "y": 20})

    async def test_transform_chat_stream_to_anthropic_sse(self):
        async def mock_stream():
            yield ChatCompletionChunk(
                id="chunk-1",
                object="chat.completion.chunk",
                created=123,
                model="claude-3-5-sonnet-20241022",
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(content="Hello"),
                        finish_reason=None,
                    )
                ],
            )
            yield ChatCompletionChunk(
                id="chunk-2",
                object="chat.completion.chunk",
                created=123,
                model="claude-3-5-sonnet-20241022",
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(content=" world"),
                        finish_reason="stop",
                    )
                ],
            )

        events: list[str] = []
        async for sse in transform_chat_stream_to_anthropic_sse(
            mock_stream(), model="claude-3-5-sonnet-20241022"
        ):
            events.append(sse)

        self.assertGreater(len(events), 4)
        self.assertTrue(any("event: message_start" in e for e in events))
        self.assertTrue(any("event: content_block_start" in e for e in events))
        self.assertTrue(any("event: content_block_delta" in e for e in events))
        self.assertTrue(any("Hello" in e for e in events))
        self.assertTrue(any("world" in e for e in events))
        self.assertTrue(any("event: message_delta" in e for e in events))
        self.assertTrue(any("event: message_stop" in e for e in events))


if __name__ == "__main__":
    unittest.main()
