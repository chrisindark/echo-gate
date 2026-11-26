# API Reference

Base URL for local development: `http://localhost:8000`. All JSON endpoints live
under `/api/v1`. Interactive docs are served at `/docs` (Swagger) and `/redoc`.

**The authoritative contract is the generated OpenAPI document**, snapshotted in
`tests/contract/snapshots/openapi.json` and enforced by
`tests/contract/test_openapi_snapshot.py`. This page documents the cross-cutting
conventions (auth, headers, errors) and indexes every operation; for exact request
and response schemas, use `/docs` or `GET /openapi.json`, or read the snapshot. If
this page and the snapshot ever disagree, the snapshot wins.

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

The completion endpoints are the exception: they return a bare
`ChatCompletionResponse` (OpenAI shape) or Anthropic `message` object for SDK
compatibility.

### Identity headers

| Header | Used by | Effect |
| --- | --- | --- |
| `X-API-Key` | analytics, gateway-requests, chat, conversations, user keys | Resolved server-side to a `tenant_id`/`user_id`; establishes the Postgres RLS context. **Required** by analytics and gateway-requests; **optional but validated** on chat and conversations. |
| `Authorization: Bearer <key>` | same | Alias for `X-API-Key` so OpenAI SDK clients work unchanged. `X-API-Key` takes precedence when both are sent. |
| `X-Admin-Key` | `/api/v1/admin/*` | Scoped operator credential. Required by every admin route; see [Admin authentication](#admin-authentication). |
| `X-Session-ID` / `X-EchoGate-Session-ID` | chat | Binds the request to a session cache scope. |
| `X-Conversation-ID` / `X-EchoGate-Conversation-ID` | chat | Binds the request to a conversation cache scope. |
| `X-Request-ID` / `X-Correlation-ID` | all | Accepted and echoed back for tracing. |

`X-API-Key` (or its Bearer alias) is the **only** source of tenant and user
identity. `tenant_id` and `user_id` are not request fields: a body carrying them
is parsed and the values discarded. The former caller-supplied `X-Tenant-ID` /
`X-User-ID` headers are ignored — honouring them let anyone who could reach the
gateway choose the tenant its request ran as.

The chat controller also accepts `session_id` and `conversation_id` in the JSON
body; body values win over headers.

### Control and optimization headers

| Header | Direction | Used by | Effect |
| --- | --- | --- | --- |
| `X-EchoGate-Prompt-Optimization` / `X-Prompt-Optimization` | Request | chat, messages, conversations | `true`, `false`, or comma-separated strategies: `whitespace`, `session-dedup`, `json-table`, `ccr`, `headroom-trim`, `compress`. |
| `X-EchoGate-Prompt-Optimization-CCR-Turns` | Request | same | Integer (default `5`): recent turns kept in full fidelity when CCR is active. |
| `X-EchoGate-Cache` / `X-Cache-Control` | Request | same | `true` to enforce, `false` / `bypass` / `off` / `no-cache` to bypass and force a fresh provider call (`X-Cache: BYPASS`). |
| `X-EchoGate-Cache-TTL` / `X-Cache-TTL` | Request | same | Integer seconds override for cache retention across Redis and Qdrant. |
| `X-EchoGate-Retries` / `X-Retries` | Request | same | Integer 0–10: maximum retry attempts against the upstream provider. |
| `X-EchoGate-Stream-Pacing` / `X-Stream-Pacing` | Request | same | `natural` / `true`, `off` / `false` / `instant`, or a float millisecond delay. |
| `X-Timeout-Budget-Ms` / `X-EchoGate-Timeout-Budget-Ms` | Request | same | Millisecond timeout budget for upstream generation. |
| `X-Cache-Prefer: instant` | Request | chat (`stream: true`) | Serve a hit as one JSON body instead of replaying SSE chunks. |
| `X-EchoGate-Prompt-Optimization` | Response | same | `applied` when optimization changed the prompt and saved tokens. |
| `X-EchoGate-Prompt-Savings` | Response | same | `tokens=<n>,chars=<n>`. |
| `X-Gateway-Degradation` | Response | same | `none`, `fallback`, `cache_degraded`, … Error responses use `none` for `4xx` and `hard_down` for terminal `5xx`. |
| `X-EchoGate-Error-Code` | Response | chat, messages | Stable classification (`provider_rate_limit`, `provider_unavailable`, …). |
| `X-Cache` | Response | chat, messages | `HIT`, `MISS`, or `BYPASS`. |
| `anthropic-version` | Response | messages | Always `2023-06-01` for Anthropic client compatibility. |

### Chat authentication

`POST /api/v1/chat/completions` runs two guards, in order:

1. **Optional API key auth.** A supplied key must resolve to an active tenant key,
   otherwise `401`. With no key the request proceeds as the `anonymous` tenant
   under the default quota limits.
2. **Quota guard** — RPM/TPM/budget enforcement.

Auth runs first so an invalid key is rejected without consuming rate-limit budget.
Supplying an unusable key is always an error; only *omitting* one is anonymous.

### Rate-limit headers

Every guarded route advertises the caller's remaining budget so clients can
self-throttle: `X-RateLimit-Limit-Requests`, `X-RateLimit-Remaining-Requests`,
`X-RateLimit-Reset-Requests`, `X-RateLimit-Limit-Tokens`,
`X-RateLimit-Remaining-Tokens`, `X-RateLimit-Reset-Tokens`, and `Retry-After`
(`429`/`503` only).

Counters are per bucket: the resolved API key for keyed callers, or
`anon:<sha256-prefix>` for anonymous ones. Reset values come from live Redis key
TTLs, clamped to the 60s window; if Redis is unreachable the gateway degrades to
"unlimited" and still emits the headers. Streamed responses carry the same
headers, set before the first chunk.

### Error behavior

The two SDK completion surfaces answer with the provider-native envelope so the
official SDKs raise typed errors; gateway-native routes keep FastAPI's
`{"detail": ...}`.

| Surface | Error body |
| --- | --- |
| `POST /api/v1/chat/completions` | `{"error": {"message", "type", "param"?, "code"?}}` (OpenAI) |
| `POST /api/v1/messages`, `POST /v1/messages` | `{"type": "error", "error": {"type", "message"}}` (Anthropic) |
| Everything else | `{"detail": ...}` |

On the OpenAI surface `code` is the gateway/provider `error_code` when one
exists, never a numeric status. Validation failures keep status `422` and name the
first failing field in `param` (OpenAI) or prefix it to the message. Any
`error_code` is also echoed in `X-EchoGate-Error-Code`.

- `400` — validation error, unsupported service, or a model outside the provider's allowlist.
- `401` — key supplied but missing, unknown, revoked, or expired.
- `402` — prepaid budget exhausted (`budget:<api_key>` ≤ 0 in Redis).
- `403` — a valid admin key lacks the scope the route requires.
- `404` — resource not found on CRUD endpoints.
- `429` — RPM/TPM quota exceeded, or global/per-provider bulkhead shedding load.
- `500` — unexpected controller error.
- `503` — no candidate provider could answer (all targets failed, breaker open, or
  a stream broke after starting). Retryable `503`s carry `Retry-After`: the longest
  remaining breaker recovery window among candidates, else
  `RETRY_AFTER_DEFAULT_SECONDS` (5).

`Retry-After` is deliberately absent from `400`, `401`, `402`, and `403`: the same
request would fail again, so a wait only wastes the caller's budget.

## Endpoint index

Every operation in the snapshot, grouped by surface. Descriptions here are short;
schemas and full parameter lists live in `/docs`.

### Completion surfaces

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/v1/chat/completions` | OpenAI-compatible chat, streaming and non-streaming. |
| `POST` | `/api/v1/messages` | Native Anthropic Messages, streaming and non-streaming. |
| `POST` | `/v1/messages` | `api/v1` alias used by Anthropic SDKs. |
| `POST` | `/api/v1/chat/estimate` | Prompt tokens + cheapest-first cost matrix across enabled models, no provider call. |
| `GET` | `/v1/models` | Model list in the envelope selected by `anthropic-version`. |
| `GET` | `/api/v1/models` | Same list. `?include_unavailable=true` with an admin key shows disabled/unconfigured models. |

### Stateful conversations

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/v1/conversations` | List (RLS-scoped). |
| `POST` | `/api/v1/conversations` | Create a thread. |
| `GET` | `/api/v1/conversations/{conversation_id}` | Thread with messages. |
| `PATCH` | `/api/v1/conversations/{conversation_id}` | Rename. |
| `DELETE` | `/api/v1/conversations/{conversation_id}` | Cascade delete. |
| `POST` | `/api/v1/conversations/{conversation_id}/chat` | History-aware completion; persists both turns atomically. |

### Health and root

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/` | App metadata. |
| `GET` | `/api/v1/health` | Basic health. |
| `GET` | `/live`, `/api/v1/health/live` | Process liveness only. |
| `GET` | `/ready`, `/api/v1/health/ready` | Readiness; gates on Postgres only (1s timeout). |
| `GET` | `/health/deps`, `/api/v1/health/deps` | Non-gating dependency roll-up (Postgres, Redis, Qdrant, Celery). Always `200`; read `status`. |
| `GET` | `/health/worker`, `/api/v1/health/worker` | Celery worker readiness. |
| `GET` | `/health/celery`, `/api/v1/health/celery` | Celery worker and broker readiness alias. |

Redis and Qdrant are degradable, so a cache blip must not evict every replica
from the load balancer: they are reported on `/health/deps`, never on `/ready`.

### Traces, analytics, shadow cache

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/v1/gateway-requests/` | List traces (`skip`, `limit`). |
| `GET` | `/api/v1/gateway-requests/{log_id}` | Trace with latency breakdown and judge scores. |
| `GET` | `/api/v1/gateway-requests/by-hash/{exact_hash}` | Lookup by exact cache hash. |
| `POST` | `/api/v1/gateway-requests/`, `PUT` `/{log_id}`, `DELETE` `/{log_id}` | CRUD for internal/admin use. |
| `GET` | `/api/v1/gateway-requests/pending-evaluations` | Rows awaiting judge evaluation. |
| `POST` | `/api/v1/gateway-requests/evaluate` | Queue evaluation for pending rows. |
| `GET` | `/api/v1/analytics/dashboard` | KPIs, cache performance, model breakdown, latency, prompt attribution. |
| `GET` | `/api/v1/analytics/latency` | Latency percentiles. |
| `GET` | `/api/v1/shadow-cache/summary` | Would-be hit rate, equivalence, projected savings, readiness verdict. |
| `GET` | `/api/v1/shadow-cache/evaluations` | Per-evaluation drill-down. |

### Usage and diagnostics

| Method | Path | Notes |
| --- | --- | --- |
| `GET`/`POST` | `/api/v1/llm-usage/` | List / create usage logs; `{log_id}` supports `GET`/`PUT`/`DELETE`. |
| `POST` | `/api/v1/llm-usage/calculate-costs` | Queue background cost calculation. |
| `POST` | `/api/v1/chat/completions/semantic-search` | Candidate retrieval only. Admin key with `cache:read`; `?tenant_id=` selects the scope. |
| `POST` | `/api/v1/chat/completions/exact-search` | Exact lookup only. Admin key with `cache:read`. |
| `POST` | `/api/v1/chat/completions/cache-save` | Write a `{request, response}` pair directly. Admin key with `cache:write`. |
| `POST` | `/api/v1/qdrant/search` | Dense search across tenants. Admin key with `cache:read`. |
| `POST` | `/api/v1/qdrant/search/rrf` | Hybrid dense + sparse RRF search. Admin key with `cache:read`. |
| `POST` | `/api/v1/verifier/predict/rerank` | Cross-encoder rerank of candidate strings. |

### Admin operations

| Method | Path | Notes |
| --- | --- | --- |
| `GET`/`POST` | `/api/v1/admin/api-keys/` | List / create keys. |
| `DELETE` | `/api/v1/admin/api-keys/{key_id}` | Revoke. |
| `POST` | `/api/v1/admin/api-keys/{key_id}/rotate` | Replace with a grace-period expiry on the old key. |
| `GET`/`POST` | `/api/v1/admin/llm-quota/` | List / create quota rules. |
| `GET`/`PATCH`/`DELETE` | `/api/v1/admin/llm-quota/{rule_id}` | Read / update / delete a rule. |
| `GET` | `/api/v1/admin/llm-quota/effective` | Resolved limits for a caller. |
| `GET` | `/api/v1/admin/llm-quota/tenants` | Tenants with rules. |
| `POST` | `/api/v1/admin/cache/warmup` | Batch `(request, response)` seeding; per-item failures reported. |
| `POST` | `/api/v1/admin/cache/search` | Semantic or exact cache search (`mode: "exact"`). |
| `DELETE` | `/api/v1/admin/cache/entry/{point_id}` | Evict one entry across Qdrant + Redis. |
| `POST` | `/api/v1/admin/cache/evict` | Filtered bulk eviction, dry-run by default, capped at 1000 per call. |

### User keys and session (Supabase auth)

Present only when Supabase auth is configured; these act on the caller's own
tenant, not through `X-Admin-Key`.

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/v1/user/me` | Authenticated session profile. |
| `GET`/`POST` | `/api/v1/user/api-keys` | List / create keys for the current tenant. |
| `DELETE` | `/api/v1/user/api-keys/{key_id}` | Revoke. |
| `POST` | `/api/v1/user/api-keys/{key_id}/rotate` | Rotate. |

## Admin authentication

`X-Admin-Key` is a separate credential type from tenant API keys. Keys live in
`admin_api_keys` and are minted only by the CLI, never over HTTP:

```bash
poetry run python -m app.scripts.create_admin_key create \
  --name "ops" --scopes "admin:*,cache:read,cache:write" --expires-in-days 90
```

- The plaintext (`ega_...`) is shown once; only a hash is stored.
- Scopes are enforced per route (`cache:read`, `cache:write`, `admin:*`).
- `admin:*` does not imply cache scopes on the diagnostic routes; grant both.
- An expired or revoked key is a `401`; an unscoped key is a `403`.

## Allowed models

`ALLOWED_MODELS` in `api/app/modules/chat/chat_schema.py` is the static allowlist;
`ALLOWED_MODELS_JSON` extends it at runtime and `MODEL_ALIASES_JSON` maps aliases
(`fast`, `cheap`, `quality`) to ranked `(provider, model)` pairs. A request for a
model outside the resolved set is a `400`. `/v1/models` reports what is actually
reachable, so prefer it over hardcoding.
