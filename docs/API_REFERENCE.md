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
| `X-API-Key` | analytics, gateway-requests, chat | Resolved server-side to a `tenant_id`/`user_id`; establishes the Postgres RLS context. **Required** by analytics and gateway-requests; **optional but validated** on chat. |
| `X-Admin-Key` | `/api/v1/admin/*` | Scoped operator credential. Required by every admin route; see [Admin authentication](#admin-authentication--x-admin-key). |
| `X-Session-ID` | chat | Binds the request to a session cache scope. |
| `X-Conversation-ID` | chat | Binds the request to a conversation cache scope. |
| `X-Request-ID` / `X-Correlation-ID` | all | Accepted and echoed back for tracing. |

`X-API-Key` is the **only** source of tenant and user identity. `tenant_id` and
`user_id` are not request fields at all — they are resolved from the key and
stamped onto the request server-side. A body carrying `tenant_id` or `user_id` is
parsed and the values discarded, and `user` in the body is not read for identity
or billing purposes.

The former `X-Tenant-ID` / `X-User-ID` headers are **ignored**. They were
caller-supplied, so honouring them let anyone who could reach the gateway choose
the tenant its request ran as — and, together with the request body, read another
tenant's cached answers.

The chat controller accepts `session_id` and `conversation_id` in the JSON body as
well as the matching headers; body values win.

### Chat authentication

`POST /api/v1/chat/completions` runs two guards, in order:

1. **Optional API key auth.** If an `X-API-Key` is supplied it must resolve to an
   active tenant key, otherwise the request is a `401`. If no key is supplied the
   request proceeds as the `anonymous` tenant — anonymous access is an intended
   feature of the chat endpoint, and those callers fall under the default quota
   limits.
2. **`llm_quota_guard`** — RPM/TPM/budget enforcement.

Auth runs first so an invalid key is rejected without consuming a request from
the rate-limit bucket. Supplying an unusable key is always an error; only
*omitting* one is anonymous.

### Error behavior

- `401` — missing or invalid key. On `/api/v1/admin/*` this means a missing,
  unknown, revoked, or expired `X-Admin-Key`; on analytics/gateway-requests it
  means a missing or invalid `X-API-Key`; on chat it means an `X-API-Key` that
  was supplied but did not resolve.
- `403` — an admin key that is valid but lacks the scope the route requires.
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
| `GET` | `/api/v1/health/ready` | Gating probe. Postgres only (1s timeout). 200/503. |
| `GET` | `/api/v1/health/deps` | Non-gating dependency report for alerting. **Always 200.** |
| `GET` | `/api/v1/health/worker` | Checks Celery broker + workers. 200/503. |
| `GET` | `/api/v1/health/celery` | Alias of `/worker`. |

### Probe tiering

The three probes answer different questions and are wired to different consumers:

| Probe | Question | Gate? | Dependencies |
| --- | --- | --- | --- |
| `/live` | Is the process running? | Restarts the container | None |
| `/ready` | Can this replica serve a *correct* response? | Load-balancer membership | Postgres |
| `/deps` | Is anything degraded? | No — alerting only | Postgres, Redis, Qdrant, Celery |

`/ready` deliberately excludes Redis and Qdrant. Both are degradable: a failed
lookup is treated as a cache miss and the router answers from the provider, so
their absence produces slower responses, never wrong ones. Postgres is not
degradable — quota rules are read through an unguarded session (a failure 500s
every chat request) and API-key resolution swallows the database error and
returns "unauthenticated" (a failure 401s valid keys). Gating on it would let a
cache blip evict every replica and turn graceful degradation into an outage.

`/deps` never returns 503, so it can be scraped without any risk of a probe
taking a replica out of rotation. Alert on the `status` field instead:

```json
{
  "status": "degraded",
  "gating": ["database"],
  "failed": ["redis"],
  "checks": {
    "database": "ok",
    "redis": "failed",
    "vector_db": "ok",
    "celery_broker": "ok",
    "celery_workers": "ok"
  }
}
```

`status` is `healthy` (all ok), `degraded` (a non-gating dependency failed), or
`unhealthy` (the gating dependency failed). Page on `unhealthy`; warn on
`degraded`.

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
| `user` | object/string | `null` | OpenAI-compatible end-user field. **Not** an identity — see [Identity headers](#identity-headers). |
| `session_id`, `conversation_id` | string | `null` | Cache key + scope. Also accepted as the `X-Session-ID` / `X-Conversation-ID` headers. |
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
    "score_breakdown": {
      "scoring_mode": "multiplicative",
      "base_score": 0.991,
      "final_score": 0.96,
      "factors": { "intent": 1.0, "metadata": 1.0, "entities": 1.0, "...": 1.0 },
      "details": { "entity_conflict": false, "metadata_mismatches": [], "metadata_comparisons": { "...": "..." } }
    },
    "intent": "code_generation",
    "point_id": "...",
    "exact_hash": "...",
    "latency_breakdown": { "total_ms": 42.1, "...": 0.0 }
  }
}
```

`cache_info.source` is one of `redis`, `qdrant_exact_path`, `qdrant_fast_path`,
`qdrant_semantic_path`, `llm`, or `mock`. `cache_info.cache_hit` distinguishes
served-from-cache from a fresh provider call. For `QDRANT_SEMANTIC` decisions,
`cache_info.score_breakdown` exposes the reranker's composite decomposition
(`scoring_mode`, `base_score`, `final_score`, per-signal `factors`, `details`);
it is also persisted on the corresponding `gateway_requests` trace row. The
response header `X-Cache` also reflects cache status.

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

## Admin authentication — `X-Admin-Key`

Every route under `/api/v1/admin/*` requires an **admin API key** sent as the
`X-Admin-Key` header. A tenant `X-API-Key` is **not** accepted there, and an
admin key is not accepted anywhere else.

Admin keys are stored separately from tenant keys (`admin_api_keys`) and carry
**scopes** rather than a tenant/user pair. They exist because the admin surface
runs in `system_rls_context()`, which disables Postgres Row-Level Security and
lets a caller read and write every tenant's rows.

### Creating a key

There is **no HTTP endpoint that mints an admin key.** Keys are created only on
the gateway host:

```bash
python -m app.scripts.create_admin_key create \
  --name ops --scopes quota:read,quota:write [--expires-in-days 90]
python -m app.scripts.create_admin_key list
python -m app.scripts.create_admin_key revoke --id 3
```

The plaintext (`ega_...`) is printed **once**; only its SHA-256 hash is stored.

| Scope | Grants |
| --- | --- |
| `quota:read` | Read quota rules, tenants, effective limits. |
| `quota:write` | Create/update/delete quota rules. Implies `quota:read`. |
| `keys:read` | List tenant API keys. |
| `keys:write` | Create/revoke tenant API keys. Implies `keys:read`. |
| `admin:*` | Everything. |

Unknown scopes are rejected at mint time (a typo like `quota:reed` fails
loudly rather than silently granting nothing).

### Behaviour

- **Fails closed.** A missing, unknown, revoked, or expired key returns `401`,
  including when no admin keys exist yet. The admin surface is locked until an
  operator mints one.
- `401` never distinguishes unknown from revoked, so the endpoint cannot be used
  to enumerate keys.
- Insufficient scope returns `403` naming the required scope and the scopes the
  key carries.
- **Revocation is not instantaneous.** Resolution is cached for 5s *per process*,
  so a revoked key can keep working for up to that long on each running worker.
- An admin key never becomes a tenant identity: `TenantContextMiddleware` only
  resolves `api_keys` rows, so `X-Admin-Key` never establishes `app.current_tenant`.

---

## API keys (admin) — `/api/v1/admin/api-keys`

Requires `X-Admin-Key` with `keys:write` to create/revoke and `keys:read` to
list.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/` | Create a key. Body `{name, tenant_id, user_id?}`. 201. |
| `GET` | `/` | List keys (metadata only; plaintext never returned). |
| `DELETE` | `/{key_id}` | Revoke a key (row remains; `is_active=false`). |

The create response includes the plaintext `api_key` **once**:

```bash
curl -X POST http://localhost:8000/api/v1/admin/api-keys/ \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: ega_..." \
  -d '{"name": "local-dev", "tenant_id": "acme", "user_id": "dev"}'
```

---

## LLM quota (admin) — `/api/v1/admin/llm-quota`

CRUD over `llm_quota_rules`, the table `llm_quota_guard` reads on every chat
completion. A rule scopes by any combination of `tenant_id` / `user_id` /
`provider` / `model`; a `null` in any of those is a wildcard. A rule with every
scope field null is **global** and applies to every tenant.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/` | Create a rule. Body `{tenant_id?, user_id?, provider?, model?, max_rpm?, max_tpm?, is_active?}`. 201. |
| `GET` | `/` | List rules. Query `skip`, `limit`, `tenant_id?`, `provider?`, `model?`, `is_active?`. |
| `GET` | `/tenants` | Distinct tenant IDs that have at least one rule. |
| `GET` | `/effective` | Most restrictive limits applying to a scope. Query `tenant_id?`, `provider?`, `model?`, `user?`. |
| `GET` | `/{rule_id}` | Fetch one rule. |
| `PATCH` | `/{rule_id}` | Partial update; omitted fields are untouched. |
| `DELETE` | `/{rule_id}` | Delete a rule permanently. |

At least one of `max_rpm` / `max_tpm` is required (`422` otherwise). A rule with
neither would never restrict anything.

```bash
curl -X POST http://localhost:8000/api/v1/admin/llm-quota/ \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "acme", "max_rpm": 60, "max_tpm": 40000}'
```

`GET /effective` resolves against the rule table only — it does not read the
Redis counters, so it reports configured limits rather than live usage. When
`uses_defaults` is true, no rule matches and the gateway falls back to its
hardcoded 10 RPM / 20000 TPM.

These handlers run inside `system_rls_context()`, so they bypass Postgres
Row-Level Security and can read and write every tenant's rules. That is what
makes cross-tenant management possible, and it is why every route requires an
`X-Admin-Key` with the matching `quota:*` scope — see
[Admin authentication](#admin-authentication--x-admin-key).

`LlmQuotaRuleService` additionally refuses to open a session unless it is handed
a resolved `AdminKeyIdentity`. That is a fail-loud guard rail, not a security
boundary (in-process Python can call the service directly), but it means a
mis-wired or forgotten guard raises instead of silently opening an
RLS-bypassed session.

### Known Gaps

- **Anonymous chat shares one rate-limit bucket.** `POST /api/v1/chat/completions`
  intentionally allows keyless requests (see above). Because the Redis counter
  key is built from `api_key`, every keyless caller lands in the same bucket —
  `rpm:None:<provider>:<model>:anonymous` — so one anonymous caller can exhaust
  the default 10 RPM and stall all other anonymous traffic. Requests carrying a
  valid `X-API-Key` use their own bucket and are unaffected. Per-caller
  anonymous isolation is a known follow-up, not an oversight.

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
