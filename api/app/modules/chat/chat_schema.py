from __future__ import annotations

import json
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr, model_validator

from app.modules.llm.model_registry import model_registry


class _AllowedModelsProxy(dict):
    def __getitem__(self, item: str) -> list[str]:
        return model_registry.get_allowed_models(item)

    def __contains__(self, item: object) -> bool:
        if isinstance(item, str):
            return item.lower() in model_registry.get_allowed_models_dict()
        return False

    def get(self, item: str, default: Any = None) -> Any:
        res = model_registry.get_allowed_models(item)
        return res if res else default


ALLOWED_MODELS = _AllowedModelsProxy()


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: FunctionCall


class FunctionDefinition(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any] | None = None
    strict: bool | None = None


class Tool(BaseModel):
    type: str = "function"
    function: FunctionDefinition


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    reasoning_content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None


class ChatCompletionRequest(BaseModel):
    service_name: str | None = "ollama"
    model: str
    messages: list[ChatMessage]
    temperature: float = 0.7
    top_p: float | None = 1.0
    n: int | None = 1
    stream: bool | None = False
    stop: list[str] | None = None
    max_tokens: int | None = None
    presence_penalty: float | None = 0.0
    frequency_penalty: float | None = 0.0
    logit_bias: dict[str, float] | None = None
    # `user` is the OpenAI-compatible end-user field. It is NOT an identity:
    # the quota guard no longer reads it, because a caller-supplied value used
    # to overwrite the identity resolved from X-API-Key and mint a fresh
    # rate-limit bucket per request.
    user: dict[str, Any] | str | None = None
    session_id: str | None = None
    conversation_id: str | None = None
    response_format: dict | None = None
    reasoning_effort: str | None = None
    thinking: bool | None = False
    thinking_budget: int | None = None
    tools: list[Tool] | None = None
    tool_choice: str | dict[str, Any] | None = None

    # Resolved identity. These are deliberately *not* body fields: they are set
    # by the chat controller from the tenant/user that ``X-API-Key`` resolved to,
    # and they must never be settable by the caller. As private attributes they
    # stay out of the request body and the OpenAPI schema (a body carrying
    # ``tenant_id`` is ignored, not honoured) while internal services can still
    # read them off the request object.
    _tenant_id: str | None = PrivateAttr(default=None)
    _user_id: str | None = PrivateAttr(default=None)

    @property
    def tenant_id(self) -> str | None:
        return self._tenant_id

    @tenant_id.setter
    def tenant_id(self, value: str | None) -> None:
        self._tenant_id = value

    @property
    def user_id(self) -> str | None:
        return self._user_id

    @user_id.setter
    def user_id(self, value: str | None) -> None:
        self._user_id = value

    @model_validator(mode="after")
    def validate_model_for_service(self) -> ChatCompletionRequest:
        if model_registry.is_alias(self.model):
            return self
        service = self.service_name or "ollama"
        if service in ALLOWED_MODELS and self.model not in ALLOWED_MODELS[service]:
            allowed = ", ".join(ALLOWED_MODELS[service])
            raise ValueError(
                f"Model '{self.model}' is not allowed for provider '{service}'. Allowed models are: {allowed}"
            )
        return self


class ChatEstimateRequest(BaseModel):
    """Input for the pre-flight cost estimate.

    Deliberately not ``ChatCompletionRequest``: the estimate prices *every*
    enabled model, so it takes only what changes the token count and never a
    provider/model pair that must pass registry validation.
    """

    messages: list[ChatMessage]
    tools: list[Tool] | None = None
    max_tokens: int | None = None
    # Tokenizer hint only. Aliases are resolved to their first target.
    model: str | None = None


class ModelCostEstimate(BaseModel):
    service: str
    model: str
    cost_prompt: float
    cost_completion: float
    cost_total: float


class ChatEstimateResponse(BaseModel):
    model: str = Field(description="Model whose tokenizer produced the counts")
    tokenizer: str = Field(
        description="Tokenizer actually used; a `heuristic` label means the BPE vocabulary was unavailable"
    )
    prompt_tokens: int
    completion_tokens: int
    estimates: list[ModelCostEstimate]


class Choice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str | None = None


class Usage(BaseModel):
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage | None = None
    system_fingerprint: str | None = None
    cache_info: dict[str, Any] | None = None

    # Set by the router: which degradation state produced this answer. Private so
    # it never leaks into the response body or the OpenAPI schema. ``None`` means
    # "not resolved yet".
    _degradation_state: str | None = PrivateAttr(default=None)


# --- Streaming Schemas & Helpers (OpenAI SSE Protocol) ---

SSE_DONE_EVENT = "data: [DONE]\n\n"


class FunctionCallChunk(BaseModel):
    name: str | None = None
    arguments: str | None = None


class ToolCallChunk(BaseModel):
    index: int
    id: str | None = None
    type: str | None = "function"
    function: FunctionCallChunk | None = None


class ChunkDelta(BaseModel):
    role: str | None = None
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCallChunk] | None = None


class ChunkChoice(BaseModel):
    index: int
    delta: ChunkDelta
    finish_reason: str | None = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: str = Field(default="chat.completion.chunk")
    created: int
    model: str
    choices: list[ChunkChoice]
    usage: Usage | None = None
    system_fingerprint: str | None = None
    cache_info: dict[str, Any] | None = None

    # Set by the router on the first chunk only (see ChatCompletionResponse).
    # ``None`` means the chunk came from a cache path, not a provider.
    _degradation_state: str | None = PrivateAttr(default=None)


def format_sse_event(data: str | BaseModel | dict[str, Any]) -> str:
    """Format data as a Server-Sent Event (SSE) line."""
    if isinstance(data, str):
        payload = data
    elif isinstance(data, BaseModel):
        payload = data.model_dump_json(exclude_none=True)
    else:
        payload = json.dumps(data)
    return f"data: {payload}\n\n"


def response_to_stream_chunks(
    response: ChatCompletionResponse,
) -> list[ChatCompletionChunk]:
    """
    Converts a full ChatCompletionResponse into a list of ChatCompletionChunk objects
    for SSE playback to streaming clients.
    """
    chunks: list[ChatCompletionChunk] = []
    base_id = response.id
    created = response.created
    model = response.model
    cache_info = response.cache_info

    for choice in response.choices:
        idx = choice.index
        msg = choice.message

        # 1. Initial role chunk
        chunks.append(
            ChatCompletionChunk(
                id=base_id,
                created=created,
                model=model,
                choices=[
                    ChunkChoice(
                        index=idx,
                        delta=ChunkDelta(role=msg.role),
                        finish_reason=None,
                    )
                ],
                cache_info=cache_info,
            )
        )

        # 1.5 Reasoning content chunk (if any)
        if msg.reasoning_content:
            chunks.append(
                ChatCompletionChunk(
                    id=base_id,
                    created=created,
                    model=model,
                    choices=[
                        ChunkChoice(
                            index=idx,
                            delta=ChunkDelta(reasoning_content=msg.reasoning_content),
                            finish_reason=None,
                        )
                    ],
                    cache_info=cache_info,
                )
            )

        # 2. Content chunk (if any)
        if msg.content:
            chunks.append(
                ChatCompletionChunk(
                    id=base_id,
                    created=created,
                    model=model,
                    choices=[
                        ChunkChoice(
                            index=idx,
                            delta=ChunkDelta(content=msg.content),
                            finish_reason=None,
                        )
                    ],
                    cache_info=cache_info,
                )
            )

        # 3. Tool calls chunk (if any)
        if msg.tool_calls:
            tool_chunks = [
                ToolCallChunk(
                    index=tc_idx,
                    id=tc.id,
                    type=tc.type,
                    function=FunctionCallChunk(
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    ),
                )
                for tc_idx, tc in enumerate(msg.tool_calls)
            ]
            chunks.append(
                ChatCompletionChunk(
                    id=base_id,
                    created=created,
                    model=model,
                    choices=[
                        ChunkChoice(
                            index=idx,
                            delta=ChunkDelta(tool_calls=tool_chunks),
                            finish_reason=None,
                        )
                    ],
                    cache_info=cache_info,
                )
            )

        # 4. Final finish chunk
        chunks.append(
            ChatCompletionChunk(
                id=base_id,
                created=created,
                model=model,
                choices=[
                    ChunkChoice(
                        index=idx,
                        delta=ChunkDelta(),
                        finish_reason=choice.finish_reason or "stop",
                    )
                ],
                usage=response.usage,
                cache_info=cache_info,
            )
        )

    return chunks


class StreamAccumulator:
    """
    Accumulates streaming chunks (text deltas, tool call chunks, usage, finish reason)
    to assemble a full ChatCompletionResponse upon stream completion for caching and logging.

    Includes memory guardrails (max_content_bytes) to prevent unbounded memory growth / OOMs
    during abnormally large responses.
    """

    def __init__(
        self,
        model: str = "",
        request_id: str | None = None,
        max_content_bytes: int = 2 * 1024 * 1024,
    ):
        self.id: str | None = request_id
        self.model: str = model
        self.created: int = int(time.time())
        self.system_fingerprint: str | None = None
        self.role: str = "assistant"
        self.content_parts: list[str] = []
        self.reasoning_parts: list[str] = []
        self.tool_calls_map: dict[int, dict[str, Any]] = {}
        self.finish_reason: str | None = None
        self.usage: Usage | None = None
        self.max_content_bytes: int = max_content_bytes
        self.current_content_bytes: int = 0
        self.overflowed: bool = False

    def add_chunk(self, chunk: ChatCompletionChunk | dict[str, Any]) -> None:
        if isinstance(chunk, dict):
            chunk = ChatCompletionChunk.model_validate(chunk)
        if not self.id and chunk.id:
            self.id = chunk.id
        if chunk.model:
            self.model = chunk.model
        if chunk.created:
            self.created = chunk.created
        if chunk.system_fingerprint:
            self.system_fingerprint = chunk.system_fingerprint
        if chunk.usage:
            self.usage = chunk.usage

        for choice in chunk.choices:
            if choice.finish_reason:
                self.finish_reason = choice.finish_reason
            delta = choice.delta
            if delta.role:
                self.role = delta.role
            if delta.reasoning_content:
                reasoning_bytes = len(delta.reasoning_content.encode("utf-8"))
                if (
                    self.current_content_bytes + reasoning_bytes
                    > self.max_content_bytes
                ):
                    self.overflowed = True
                else:
                    self.reasoning_parts.append(delta.reasoning_content)
                    self.current_content_bytes += reasoning_bytes
            if delta.content:
                content_bytes = len(delta.content.encode("utf-8"))
                if self.current_content_bytes + content_bytes > self.max_content_bytes:
                    self.overflowed = True
                else:
                    self.content_parts.append(delta.content)
                    self.current_content_bytes += content_bytes
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    args_bytes = (
                        len(tc.function.arguments.encode("utf-8"))
                        if (tc.function and tc.function.arguments)
                        else 0
                    )
                    if self.current_content_bytes + args_bytes > self.max_content_bytes:
                        self.overflowed = True

                    if idx not in self.tool_calls_map:
                        self.tool_calls_map[idx] = {
                            "id": tc.id or "",
                            "type": tc.type or "function",
                            "name": (
                                tc.function.name
                                if tc.function and tc.function.name
                                else ""
                            ),
                            "arguments_parts": (
                                [tc.function.arguments]
                                if tc.function
                                and tc.function.arguments
                                and not self.overflowed
                                else []
                            ),
                        }
                    else:
                        if tc.id:
                            self.tool_calls_map[idx]["id"] = tc.id
                        if tc.type:
                            self.tool_calls_map[idx]["type"] = tc.type
                        if tc.function:
                            if tc.function.name:
                                self.tool_calls_map[idx]["name"] = tc.function.name
                            if tc.function.arguments and not self.overflowed:
                                self.tool_calls_map[idx]["arguments_parts"].append(
                                    tc.function.arguments
                                )
                    if not self.overflowed and args_bytes > 0:
                        self.current_content_bytes += args_bytes

    def build_response(self) -> ChatCompletionResponse:
        content = "".join(self.content_parts) if self.content_parts else None
        reasoning_content = (
            "".join(self.reasoning_parts) if self.reasoning_parts else None
        )
        tool_calls = None
        if self.tool_calls_map:
            tool_calls = []
            for idx in sorted(self.tool_calls_map.keys()):
                entry = self.tool_calls_map[idx]
                tool_calls.append(
                    ToolCall(
                        id=entry["id"] or f"call_{uuid.uuid4().hex}",
                        type=entry["type"] or "function",
                        function=FunctionCall(
                            name=entry["name"],
                            arguments="".join(entry["arguments_parts"]),
                        ),
                    )
                )

        return ChatCompletionResponse(
            id=self.id or f"chatcmpl-{uuid.uuid4().hex}",
            object="chat.completion",
            created=self.created,
            model=self.model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role=self.role,
                        content=content,
                        reasoning_content=reasoning_content,
                        tool_calls=tool_calls,
                    ),
                    finish_reason=self.finish_reason
                    or (
                        "length"
                        if self.overflowed
                        else ("tool_calls" if tool_calls else "stop")
                    ),
                )
            ],
            usage=self.usage,
            system_fingerprint=self.system_fingerprint,
        )
