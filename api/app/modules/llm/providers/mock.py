"""Deterministic mock provider used by tests and `LLM_SERVICE=mock`.

Extracted verbatim from the former single-file `LlmProviderService`. The methods
still operate on the dispatcher instance (``self``), which owns every provider
credential and client; only the owning class changed.
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator

from app.core.constants import MOCK_RESPONSE_TEXT
from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    ChunkChoice,
    ChunkDelta,
    FunctionCall,
    FunctionCallChunk,
    ToolCall,
    ToolCallChunk,
    Usage,
)

logger = logging.getLogger(__name__)


class MockProvider:
    def generate_mock_response(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        if request.tools and request.tool_choice != "none":
            target_fn_name = None
            if isinstance(request.tool_choice, dict):
                target_fn_name = request.tool_choice.get("function", {}).get("name")
            if not target_fn_name and request.tools:
                target_fn_name = request.tools[0].function.name

            mock_tool_call = ToolCall(
                id=f"call_{uuid.uuid4().hex}",
                type="function",
                function=FunctionCall(
                    name=target_fn_name or "mock_tool", arguments="{}"
                ),
            )
            return ChatCompletionResponse(
                id=f"chatcmpl-{uuid.uuid4().hex}",
                created=int(time.time()),
                model=request.model,
                choices=[
                    Choice(
                        index=0,
                        message=ChatMessage(
                            role="assistant",
                            content=None,
                            tool_calls=[mock_tool_call],
                        ),
                        finish_reason="tool_calls",
                    )
                ],
                usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
                system_fingerprint="mock",
            )

        reasoning_content = (
            "Analyzing the user request step by step..."
            if (request.thinking or request.reasoning_effort)
            else None
        )
        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex}",
            created=int(time.time()),
            model=request.model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content=MOCK_RESPONSE_TEXT,
                        reasoning_content=reasoning_content,
                    ),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            system_fingerprint="mock",
        )

    async def generate_mock_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
        created_time = int(time.time())

        # 1. Role chunk
        yield ChatCompletionChunk(
            id=chunk_id,
            created=created_time,
            model=request.model,
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChunkDelta(role="assistant"),
                    finish_reason=None,
                )
            ],
            system_fingerprint="mock",
        )

        if request.tools and request.tool_choice != "none":
            target_fn_name = None
            if isinstance(request.tool_choice, dict):
                target_fn_name = request.tool_choice.get("function", {}).get("name")
            if not target_fn_name and request.tools:
                target_fn_name = request.tools[0].function.name

            tool_id = f"call_{uuid.uuid4().hex}"
            yield ChatCompletionChunk(
                id=chunk_id,
                created=created_time,
                model=request.model,
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(
                            tool_calls=[
                                ToolCallChunk(
                                    index=0,
                                    id=tool_id,
                                    type="function",
                                    function=FunctionCallChunk(
                                        name=target_fn_name or "mock_tool",
                                        arguments="{}",
                                    ),
                                )
                            ]
                        ),
                        finish_reason=None,
                    )
                ],
                system_fingerprint="mock",
            )
            yield ChatCompletionChunk(
                id=chunk_id,
                created=created_time,
                model=request.model,
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(),
                        finish_reason="tool_calls",
                    )
                ],
                usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
                system_fingerprint="mock",
            )
        else:
            if request.thinking or request.reasoning_effort:
                yield ChatCompletionChunk(
                    id=chunk_id,
                    created=created_time,
                    model=request.model,
                    choices=[
                        ChunkChoice(
                            index=0,
                            delta=ChunkDelta(
                                reasoning_content="Analyzing the user request step by step..."
                            ),
                            finish_reason=None,
                        )
                    ],
                    system_fingerprint="mock",
                )

            words = MOCK_RESPONSE_TEXT.split(" ")
            for i, word in enumerate(words):
                token = word + (" " if i < len(words) - 1 else "")
                yield ChatCompletionChunk(
                    id=chunk_id,
                    created=created_time,
                    model=request.model,
                    choices=[
                        ChunkChoice(
                            index=0,
                            delta=ChunkDelta(content=token),
                            finish_reason=None,
                        )
                    ],
                    system_fingerprint="mock",
                )
            yield ChatCompletionChunk(
                id=chunk_id,
                created=created_time,
                model=request.model,
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChunkDelta(),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
                system_fingerprint="mock",
            )
