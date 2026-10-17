# API Reference

Base URL for local development: `http://localhost:8000`. All JSON endpoints live
under `/api/v1`. Interactive docs are served at `/docs` (Swagger) and `/redoc`.

The authoritative contract is the generated OpenAPI document, snapshotted in
`tests/snapshots/openapi.json` and enforced by `tests/test_openapi_snapshot.py`.
This reference matches that snapshot; if they ever disagree, the snapshot (and
`app.openapi()`) wins.

## Conventions

### Response envelope

Most non-chat endpoints wrap their payload in `BaseAPIResponse`:

```json
{
  "success": true,
  "response": { },
  "status_code": 200,
  "error_message": null,
  "error_code": null
}
```

The chat completion endpoint is the exception: it returns a bare
`ChatCompletionResponse` (OpenAI shape) for compatibility.

### Identity headers

| Header | Used by | Effect |
| --- | --- | --- |
| `X-API-Key` | all (required by chat) | Resolved server-side to a `tenant_id`/`user_id`; establishes the Postgres RLS context. |
| `X-Tenant-ID` | trusted/internal | Tenant when no API key is supplied. |
| `X-User-ID` | trusted/internal | User when no API key is supplied. |
| `X-Session-ID` | chat | Binds the request to a session cache scope. |
| `X-Conversation-ID` | chat | Binds the request to a conversation cache scope. |
| `X-Request-ID` / `X-Correlation-ID` | all | Accepted and echoed back for tracing. |

The chat controller also accepts `tenant_id`, `user_id`, `session_id`, and
`conversation_id` in the JSON body; body values win, then middleware/header values
apply.

### Error behavior

- `401` — `llm_quota_guard` requires `X-API-Key` on `POST /chat/completions`;
  missing key → `{"detail": "API Key missing"}`.
- `429` — RPM or TPM quota exceeded (see `llm_quota`).
- `402` — prepaid budget exhausted (`budget:<api_key>` ≤ 0 in Redis).
- `400` — validation error, unsupported service, or a model not in the
  provider's allowlist.
- `503` — provider (and fallback) failed.
- `500` — unexpected server error in a controller.
- `404` — resource not found on CRUD endpoints.

---

## Root

### `GET /`

Returns app metadata.

```json
{
  "success": true,
  "response": { "app": "echo-gate", "version": "0.1.0", "docs": "/docs", "api_prefix": "/api/v1" },
  "status_code": 200
}
```

---

## Health — `/api/v1/health`

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/v1/health` | Basic check. `{"status": "healthy", "version": ...}`. |
| `GET` | `/api/v1/health/live` | Process liveness. 200 healthy, 503 otherwise. |
| `GET` | `/api/v1/health/ready` | Checks Postgres, Redis, Qdrant (1s timeouts). 200/503. |
| `GET` | `/api/v1/health/worker` | Checks Celery broker + workers. 200/503. |
| `GET` | `/api/v1/health/celery` | Alias of `/worker`. |

---

## Chat — `/api/v1/chat`

### `POST /api/v1/chat/completions`

The main gateway endpoint. OpenAI-compatible request; returns a
`ChatCompletionResponse`. Requires `X-API-Key`.

**Request body** (`ChatCompletionRequest`):

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `service_name` | string | `"ollama"` | Provider. `ollama`, `gemini`/`google-genai`, `openai`, `groq`, `openrouter`. |
| `model` | string | required | Must be in [`ALLOWED_MODELS`](#allowed-models). |
| `messages` | array | required | `{role, content, name?, tool_call_id?, tool_calls?}`. |
| `temperature` | float | `0.7` | Part of the cache key and confidence gate. |
| `top_p` | float | `1.0` | Cache key. |
| `n` | int | `1` | Accepted, not separately handled by cache. |
| `stream` | bool | `false` | When `true`, returns Server-Sent Events (`text/event-stream`) emitting `ChatCompletionChunk` SSE payloads and ending with `data: [DONE]`. |
| `stop` | string[] | `null` | Cache key via `stop_hash`. |
| `max_tokens` | int | `null` | Cache key; also filters candidates by completion tokens. |
| `presence_penalty` / `frequency_penalty` | float | `0.0` | Cache key. |
| `logit_bias` | object | `null` | Cache key. |
| `user` | object/string | `null` | Falls back to middleware user. |
| `user_id`, `tenant_id`, `session_id`, `conversation_id` | string | `null` | Cache key + scope. |
| `response_format` | object | `null` | `{"type":"json_object"}` or `{"type":"json_schema", ...}`. Cache key via `response_format_hash`. |
| `reasoning_effort` | string | `null` | Passed to reasoning-capable providers (`low`, `medium`, `high`). Cache key. |
| `thinking` | bool | `false` | Enable extended thinking / reasoning mode across Gemini, Claude, OpenAI, and Ollama. Cache key. |
| `thinking_budget` | int | `null` | Maximum token budget for reasoning (e.g. 1024-16384). Cache key. |
| `tools` | array | `null` | OpenAI tool schema. **Presence bypasses the semantic cache.** |
| `tool_choice` | string/object | `null` | Cache key. |

**Response** (`ChatCompletionResponse`):

```json
{
  "id": "...",
  "object": "chat.completion",
  "created": 1710000000,
  "model": "gemini-3.5-flash-lite",
  "choices": [{ "index": 0, "message": {"role": "assistant", "content": "...", "reasoning_content": "..."}, "finish_reason": "stop" }],
  "usage": { "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30 },
  "system_fingerprint": null,
  "cache_info": {
    "matched": true,
    "cache_hit": true,
    "source": "qdrant_semantic_path",
    "score": 0.97,
    "rerank_score": 0.96,
    "intent": "code_generation",
    "point_id": "...",
    "exact_hash": "...",
    "latency_breakdown": { "total_ms": 42.1, "...": 0.0 }
  }
}
```

`cache_info.source` is one of `redis`, `qdrant_exact_path`, `qdrant_fast_path`,
`qdrant_semantic_path`, `llm`, or `mock`. `cache_info.cache_hit` distinguishes
served-from-cache from a fresh provider call. The response header `X-Cache` also
reflects cache status.

```bash
curl -X POST "http://localhost:8000/api/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <key>" \
  -d '{
    "service_name": "google-genai",
    "model": "gemini-3.5-flash-lite",
    "messages": [{"role": "user", "content": "Explain quantum computing in one paragraph."}]
  }'
```

### `POST /api/v1/chat/completions/semantic-search`

Diagnostic: runs only the semantic cache path and returns its internal result
(matches, intent, embeddings, latency breakdown) without calling a provider.
Same `ChatCompletionRequest` body. Wrapped in `BaseAPIResponse`.

### `POST /api/v1/chat/completions/exact-search`

Diagnostic: returns the Qdrant exact-match payload and `point_id` for the request's
exact hash, without a provider call.

### `POST /api/v1/chat/completions/cache-save`

Diagnostic: writes a `{request, response}` pair directly into the cache.
Body: `{ "request": ChatCompletionRequest, "response": ChatCompletionResponse }`.
Returns `{"point_id": ...}`.

---

## Gateway request traces — `/api/v1/gateway-requests`

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/` | Create a trace log. Body `GatewayRequestLogCreate`. |
| `GET` | `/` | Filtered, paginated list. Returns `{items, total, skip, limit}`. |
| `GET` | `/pending-evaluations` | Traces awaiting evaluation (`?limit=`). |
| `PUT` | `/{log_id}` | Update evaluation fields. Body `GatewayRequestLogUpdate`. |
| `GET` | `/{log_id}` | Fetch one trace. |
| `GET` | `/by-hash/{exact_hash}` | Fetch a trace by its exact cache hash. |
| `DELETE` | `/{log_id}` | Delete a trace. |
| `POST` | `/evaluate` | Queue background evaluation (Celery, else FastAPI background). |

`GET /` query parameters: `skip`, `limit` (1–200), `tenant_id`, `user_id`,
`routing_decision`, `provider`, `model`, `search`, `min_latency_ms`,
`max_latency_ms`, `evaluation_status`, `is_false_positive`, `has_error`,
`time_range` (`1h|3h|6h|12h|1d|3d|7d|14d|30d`), `start_time`, `end_time`.

`RoutingDecision`: `LLM`, `REDIS_EXACT`, `QDRANT_EXACT`, `QDRANT_SEMANTIC`,
`QDRANT_FAST`. `EvaluationStatus`: `PENDING`, `IN_PROGRESS`, `EVALUATED`,
`SKIPPED`, `FAILED`. Full field list in [DATA_MODEL.md](DATA_MODEL.md).

---

## Analytics — `/api/v1/analytics`

### `GET /api/v1/analytics/dashboard`

Aggregated dashboard data. Query: `time_range`, `start_time`, `end_time`,
`tenant_id`. Response `AnalyticsDashboardResponse`:

```json
{
  "kpis": { "total_requests": 0, "total_cost": 0.0, "total_tokens": 0, "avg_latency_ms": 0.0, "cache_hit_rate": 0.0 },
  "time_series": [{ "date_label": "2026-10-01", "requests": 0, "cost": 0.0, "tokens": 0 }],
  "cache_performance": { "llm_calls": 0, "redis_exact": 0, "qdrant_exact": 0, "qdrant_semantic": 0, "total": 0, "cache_hit_percentage": 0.0 },
  "model_breakdown": [{ "model": "...", "provider": "...", "requests": 0, "total_cost": 0.0, "total_tokens": 0 }],
  "evaluations": { "avg_relevance_score": null, "avg_contradiction_score": null, "avg_entailment_score": null, "false_positive_rate": null, "evaluated_count": 0 },
  "top_intents": [{ "intent": "code_generation", "count": 0 }],
  "latency_metrics": { "...": "LatencyMetricsResponse" }
}
```

> The dashboard already contains the KPI and cache-performance data. There are no
> separate `/kpis` or `/cache-performance` endpoints (older READMEs referenced
> them).

### `GET /api/v1/analytics/latency`

`LatencyMetricsResponse`: `total_requests`, `avg_latency_ms`,
`avg_provider_latency_ms`, `avg_cache_lookup_latency_ms`, `percentiles`
(`p50`/`p90`/`p95`/`p99`), `component_breakdown` (the per-stage ms fields),
`by_routing_decision[]`, and `time_series[]`. Query params as above.

---

## LLM usage — `/api/v1/llm-usage`

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | List usage logs. Query `start_time`, `end_time`, `skip`, `limit` (1–1000). |
| `GET` | `/{log_id}` | Fetch one usage log. |
| `POST` | `/` | Create a usage log (`LlmUsageLogCreate`). 201. |
| `PUT` | `/{log_id}` | Update a usage log (`LlmUsageLogUpdate`). |
| `DELETE` | `/{log_id}` | Delete a usage log. |
| `POST` | `/calculate-costs` | Queue background cost calculation. |

`POST /calculate-costs` returns
`{"task_id", "dispatched_to_celery", "message"}`.

---

## Qdrant — `/api/v1/qdrant`

Diagnostic vector-search endpoints.

- `POST /api/v1/qdrant/search` — dense search over `user_prompt_embedding` (or
  `system_prompt_embedding`). Body `QdrantSearchRequest`
  (`messages`, `threshold` default `0.90`, `model_filter`).
  Returns `{found, payload}`.
- `POST /api/v1/qdrant/search/rrf` — same body, hybrid dense+sparse RRF search.

---

## Verifier — `/api/v1/verifier`

- `POST /api/v1/verifier/predict/rerank` — cross-encoder rerank of candidates.
  Body `{"query": "...", "candidates": ["..."]}`. Returns
  `{"results": [{"candidate": "...", "score": 0.0}]}` sorted by descending score.

---

## API keys (admin) — `/api/v1/admin/api-keys`

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/` | Create a key. Body `{name, tenant_id, user_id?}`. 201. |
| `GET` | `/` | List keys (metadata only; plaintext never returned). |
| `DELETE` | `/{key_id}` | Revoke a key (row remains; `is_active=false`). |

The create response includes the plaintext `api_key` **once**:

```bash
curl -X POST http://localhost:8000/api/v1/admin/api-keys/ \
  -H "Content-Type: application/json" \
  -d '{"name": "local-dev", "tenant_id": "acme", "user_id": "dev"}'
```

---

## Conversations — `/api/v1/conversations` *(not mounted)*

`app/modules/conversation/conversation_controller.py` defines:

- `POST /api/v1/conversations`
- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{conversation_id}`
- `POST /api/v1/conversations/{conversation_id}/chat`

**But the router is not registered in `app/main.py`, so all four return 404
today.** The tables and RLS policies exist (see
[DATA_MODEL.md](DATA_MODEL.md)); registering the router is the missing step.

---

## Allowed models

Enforced by `validate_model_for_service` in `app/modules/chat/chat_schema.py`.
Sending a model outside its provider's list returns `400`.

| Provider (`service_name`) | Allowed models |
| --- | --- |
| `ollama` | `qwen3:8b`, `qwen2.5-coder:14b`, `qwen2.5-coder:1.5b`, `qwen2.5-coder:3b`, `qwen2.5-coder:7b` |
| `gemini`, `google-genai` | `gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`, `gemini-3.8-flash` |
| `openai` | `gpt-4o-mini` |
| `groq` | `openai/gpt-oss-20b`, `openai/gpt-oss-safeguard-20b` |
| `openrouter` | `qwen/qwen3.8-27b:free`, `google/gemma-4-26b-a4b-it:free`, `google/gemma-4-31b-it:free` |

Pricing (`app/core/pricing.py`) is per 1M tokens; Ollama, Groq, and OpenRouter
free models are `0.0`. OpenAI `gpt-4o-mini`: $0.15 / $0.60. Gemini
`gemini-3.1-flash-lite` and `gemini-3.8-flash`: $0.075 / $0.30;
`gemini-3.5-flash-lite`: $1.25 / $5.00.
