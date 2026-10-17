# Echo Gate — Full Streaming Support (`stream: true`) — Implementation Plan

Plan for implementing the "Full Streaming Support" feature described in `plans.md`
(section 2). Written 2026-10-16. This feature is NOT yet implemented; only the
planning below exists.

## 1) Goals & Acceptance Criteria

- [ ] Request schema accepts `stream` (bool, default false).
- [ ] Router branches on `stream` (separate path from non-streaming).
- [ ] Provider adapters can stream SSE chunks for Ollama, Gemini, OpenAI, (and
      optionally Groq/OpenRouter).
- [ ] Cache miss with `stream=true`: stream chunks to client immediately; accumulate
      full text + tool_calls + usage; after successful termination
      (`finish_reason == "stop"`) dispatch a background async task to write the
      completed completion to Redis and Qdrant. Do not write on error/abort.
- [ ] Cache hit with `stream=true`: simulate a chunked SSE stream built from cached
      full completion so client parsers receive expected SSE events; terminate with
      `data: [DONE]`.
- [ ] Failure safety: if a stream errors mid-flight, abort cache write and propagate
      error to client.
- [ ] Streaming does not affect cache keying (tools already handled).
      Tenant/model/provider-sensitive behavior preserved.
- [ ] Backwards compatible: existing non-streaming calls unchanged.
- [ ] OpenAI-compatible SSE format where possible.

## 2) High-Level Flow

### Cache miss (stream)
1. Client sends `POST /chat/completions` with `stream: true`.
2. Router checks cache (exact + semantic) — miss.
3. Router calls provider's streaming method -> async iterator of provider chunks.
4. Controller/router wraps iterator to transform provider chunk -> OpenAI-style SSE
   chunk (`data: {...}\n\n`) while tee-ing/accumulating contenido, tool_calls,
   usage, finish_reason.
5. As chunks stream to client, accumulate `full_text`, `tool_calls`, `usage`,
   `finish_reason`, `model`, `id`, `created` in memory.
6. On stream completion with `finish_reason == "stop"` (and no error), schedule
   background task `store_streamed_completion(...)` writing to Redis (exact key)
   and Qdrant (semantic entry) using accumulated data.
7. On stream error/close with error, do NOT schedule write; log minimally.

### Cache hit (stream)
1. Cache lookup returns cached full completion (Redis exact or Qdrant semantic
   match).
2. Router builds async generator that emits cached content as SSE chunks:
   role-first chunk, then content slice(s), final chunk
   `finish_reason: "stop"`, then `data: [DONE]`.
3. Return StreamingResponse with that generator.

## 3) Data Model / Schema Changes

### Chat request schema (`app/modules/chat/chat_schema.py`)
Add to request model (OpenAI-compatible):

- `stream: bool | None = Field(default=False)`
- Ensure existing fields (`tools`, `tool_choice`, ...) unchanged.

### Streaming response format
Emit OpenAI Chat Completions streaming format (chunked):

```
data: {"id": "...", "object": "chat.completion.chunk", "created": ..., "model": ..., "choices": [{"index": 0, "delta": {...}, "finish_reason": null}]}

data: {"choices": [{"delta": {"content": "..."}, "finish_reason": null}]}

data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}

data: [DONE]
```

Generate/propagate `id`, `created`, `model`, `usage` when provider sends them
(often on final chunk).

### Accumulated streamed result
Small internal closure state (`dict` or dataclass) with:
- `content_parts: list[str]` -> joined `full_text`
- `tool_calls: list[...] | None`
- `usage: dict | None`
- `finish_reason: str | None`
- `model`, `id`, `created`, `error`

## 4) Provider Layer (`app/modules/llm/llm_provider_service.py`)

Add streaming methods yielding parsed chunk dicts, so the router can normalize.

### OpenAI (and Groq/OpenRouter, which are OpenAI-compatible)
- Use `httpx.AsyncClient.stream("POST", ...)` with JSON body `stream=True`.
- Iterate lines; for each `data: ...` parse JSON. Skip `data: [DONE]`.
- Yield chunk dicts preserving `choices[0].delta`, `finish_reason`, `usage`.
- Raise on HTTP error, preserving the exception object (mirroring router fix).

### Ollama
- Ollama `/api/chat` with `stream=true` returns line-delimited JSON objects:
  `{"message": {...}, "done": false, ...}`.
- On `done=True` it may include `total_duration`, `prompt_eval_count`,
  `eval_count` (usage).
- Transform each to OpenAI chunk format: delta from `message.content` /
  `message.tool_calls`, set `finish_reason="stop"` on done.

### Gemini (google-genai)
- Use `generate_content_stream` (async iterator); each chunk has candidates/content parts.
- Map to OpenAI chunk format: accumulate text from parts, set
  `finish_reason` from candidate; handle empty deltas; be defensive about
  safety blocks/errors.

### Error handling
- Raise on HTTP errors (preserve exception object).
- On malformed chunks: log and continue? Prefer fail-fast and skip cache write.

## 5) Router Layer (`app/modules/llm/llm_router_service.py`)

Add a streaming entry point. Either extend `generate_completion` to detect and
branch on `request.stream`, or add `generate_completion_stream(...)`.

### Cache miss path
- Get provider adapter, call its stream method, wrap to:
  - yield normalized SSE chunks to caller (controller)
  - collect accumulated data in closure
  - on exit, if `finish_reason == "stop"` and no exception, schedule
    `asyncio.create_task(store_streamed_completion(...))`

### Cache hit path
- Build simulated chunk stream from cached full completion (reuse accumulated
  data shape).

### Preserve keying
Tools payload already part of cache key (from prior fix). No changes needed;
keep tenant/model/provider sensitivity.

## 6) Controller (`app/modules/chat/chat_controller.py`)

Return `StreamingResponse` when streaming:

- If `request.stream`:
  - Build async generator yielding `f"data: {json.dumps(chunk)}\n\n"` lines.
  - Final yield `f"data: [DONE]\n\n"`.
  - `StreamingResponse(generator(), media_type="text/event-stream")` with
    headers `Cache-Control: no-cache`, `Connection: keep-alive`.
- Else existing JSON response unchanged.

## 7) Cache Write After Stream

Need a background write path using accumulated data.

- Use `asyncio.create_task` in the router's streaming wrapper (runs in same event
  loop as request) — matches the planned approach.
- Write identical shape to non-streaming cache:
  - Redis exact: store full completion JSON under exact key (choices/content,
    model, usage, tool_calls, id/created).
  - Qdrant semantic: store semantic entry with full text + metadata
    (tenant/model/provider).
- Do not write if stream errored or aborted before `finish_reason == "stop"`.
- If the background write fails after stream sent: that's acceptability-degraded
  (cache miss next time) — don't affect client; log it.

## 8) Cache Hit Simulation

From cached full text (e.g. `cached["choices"][0]["message"]["content"]`), emit:

- First chunk: `delta.role = "assistant"`, `delta.content = ""` (or first slice).
- Subsequent chunks: split text into small chunks (~128–256 chars on word
  boundaries) — keep simple (even 2–4 chunks is acceptable).
- Final chunk: `delta = {}`, `finish_reason = "stop"`.
- Then `data: [DONE]`.
- Include `id`, `model`, `created` from cached entry when present.

## 9) Failure Safety

- If provider stream raises: don't schedule cache task. Let exception bubble to
  StreamingResponse.
- Track `stream_error` flag in accumulator; only write if `finish_reason == "stop"`
  and no error.
- Try/finally around accumulation so partial state isn't cached even if the
  generator exits early (e.g. client disconnect).

## 10) Config / Edge Cases

- Empty content is valid (tool calls only).
- Usage may only arrive on final chunk (OpenAI) — capture if present.
- Gemini may send multiple parts and tool calls.
- Preserve provider stream timeouts (mirror existing httpx/provider timeout).
- Memory: accumulating full text is required to write caches after stream ends; it
  matches plans explicitly.
- Client disconnect mid-stream: ASGI closes generator; task scheduling happens on
  successful completion only, so no write.

## 11) Files to Modify (in order)

1. `app/modules/chat/chat_schema.py` — add `stream`.
2. `app/modules/llm/llm_provider_service.py` — add stream generators for ollama,
   gemini, openai, (groq/openrouter reusable).
3. `app/modules/llm/llm_router_service.py` — streaming hit/miss paths, accumulation
   wrapper, task dispatch.
4. `app/modules/chat/chat_controller.py` — `StreamingResponse` branch.
5. `app/modules/qdrant/llm_cache_collection_service.py` — maybe a helper to write
   from accumulated dict; reuse existing store logic.
6. `app/modules/redis/redis_service.py` — no change expected.
7. (optional) `app/modules/llm/llm_schema.py` if needed for stream types.

## 12) Implementation Steps (concrete order)

1. Schema: add `stream`.
2. Provider streaming (OpenAI): `stream_generate_openai` async generator.
3. Provider streaming (Ollama): map line-delimited JSON.
4. Provider streaming (Gemini): map `generate_content_stream`.
5. Provider streaming (Groq/OpenRouter): reuse OpenAI mapping.
6. Router — cache hit simulation.
7. Router — cache miss streaming (wrap + accumulate + task dispatch).
8. Controller — `StreamingResponse` branch.
9. Background store — `store_streamed(...)` to Redis + Qdrant, same shape as
   non-streaming path.
10. Failure safety — guard writes; try/finally.
11. Tests: chunk mapping, hit simulation, miss accumulation + no-write-on-error.

## 13) Test Plan

- Schema: `stream=true/false` parsed.
- OpenAI chunk mapping preserves delta/usage/finish_reason.
- Ollama `done -> finish_reason stop` + usage.
- Gemini parts -> content deltas.
- Cache hit stream emits chunks + `[DONE]`; client receives all.
- Cache miss stream emits ordered chunks, stores once on stop, never on error.
- Tools in stream: tool_calls deltas preserved.
- Tenant isolation unchanged.

## 14) Risks & Mitigations

- Memory: accumulates full text — acceptable; avoid duplicating large objects.
- Background task after send: `asyncio.create_task` fine; ensure no strong reference
  cycles. Loop exits only on shutdown (rare).
- Provider format drift: test against current SDK versions.
- Double write: only miss path, only after successful stop.
- Semantic cache hit during stream: checked before provider stream (same as
  non-stream). Correct.

## 15) Suggested Starting Points

- Mirror existing provider methods in `llm_provider_service.py` (httpx usage,
  error handling, payload construction).
- Reuse store logic in `llm_router_service.py` (`store_in_redis_and_qdrant`) when
  building `store_streamed`.
- Add `StreamingResponse` import in `chat_controller.py`.

Implementation can proceed stepwise, starting with schema + OpenAI streaming and
moving to router integration. Let me know when you resume next session.
