# Architecture

Echo Gate is a FastAPI service that presents an OpenAI-compatible
`chat/completions` API in front of several LLM providers, with a multi-level
semantic cache in front of the providers. A separate Next.js app in `web/` renders
the chat playground, trace explorer, and analytics dashboards.

## System components

| Component | Where | Role |
| --- | --- | --- |
| HTTP API | `api/app/main.py`, `api/app/modules/*/*_controller.py` | FastAPI app, middleware, routers. |
| Routing/cache core | `api/app/modules/llm/llm_router_service.py` | Owns the request lifecycle: exact cache, semantic cache, verification, provider call, cache write, logging. |
| Provider adapters | `api/app/modules/llm/llm_provider_service.py` | One method per provider: `google-genai`/`gemini`, `openai`, `groq`, `openrouter`, `ollama`. |
| Embeddings | `api/app/modules/embedding/embedding_service.py` | Dense embeddings (remote via `inference-platform/`, or `sentence-transformers` in-process) and sparse lexical embeddings (BM25, always local). |
| Inference platform | `api/app/modules/inference/inference_client.py` | HTTP client for the shared model server: embeddings, reranking, relevance, NLI. Fail-open; owns role routing and error classification. |
| Redis cache | `api/app/modules/redis/redis_service.py` | Exact-match cache and quota counters. |
| Qdrant cache | `api/app/modules/qdrant/llm_cache_collection_service.py` | Vector cache collection `embeddings_multivector`: upsert, exact search, dense search, hybrid RRF search, expiry deletion. |
| Intent + entities | `api/app/modules/intent_classifier/` | LLM-based intent classification and regex-based entity extraction; scope/TTL decisions. |
| Reranking/verification | `api/app/modules/reranking/reranker_service.py`, `api/app/modules/llm/cache_confidence.py`, `api/app/modules/verifier/` | Cross-encoder rerank, confidence gate, and the async judge/cross-encoder evaluators. |
| Relational store | `api/app/core/database.py`, `api/app/modules/*/*_model.py`, `alembic/` | SQLAlchemy models on PostgreSQL with Row-Level Security. |
| Background workers | `api/app/worker/celery_app.py`, `*/tasks.py` | Celery worker + Beat: cache eviction, cost calculation, pending-request evaluation. |
| UI | `web/` | Next.js 16 + React 19 observability dashboard. |
| DI container | `api/app/core/dependencies.py` | Builds every singleton service and tears them down on shutdown. |

## Module layout

```text
api/app/
  main.py                     FastAPI app, lifespan, middleware, router registration
  core/
    config.py                 Environment-backed settings (single Config instance)
    dependencies.py           DependencyContainer: constructs/shares services
    database.py               SQLAlchemy engines/sessions (read + write)
    rls.py                    Postgres RLS GUC context (tenant/user/system)
    middlewares.py            CorrelationId, User, TenantContext middleware
    guards.py                 llm_quota_guard (RPM/TPM/budget enforcement),
                              ApiKeyAuthGuard (tenant; strict + optional modes),
                              AdminApiKeyGuard (scoped admin)
    retry.py                  tenacity-based retry (status 408/429/5xx + timeouts)
    responses.py              BaseAPIResponse envelope
    time_ranges.py            Shared time-range resolution for read endpoints
    pricing.py                Per-provider/model token pricing
    constants.py              Version tags, scope hierarchy, prefixes
    logger.py                 Structured/colored logging + correlation IDs
  modules/
    chat/                     chat schema + controller (the public completions API)
    llm/                      provider adapters, router service, prompts, confidence
    embedding/                dense + sparse embedding service
    inference/                HTTP client for the shared inference platform
    redis/                    Redis client wrapper
    qdrant/                   Qdrant client, cache collection service, controller, schema, tasks
    intent_classifier/        intent LLM service, regex entity extractor, intent schema
    reranking/                cross-encoder reranker
    verifier/                 cross-encoder + judge + instruction verifier, controller
    gateway_requests/         trace model/schema/service/controller + evaluation task
    llm_usage/                usage model/schema/service/controller + cost task
    llm_quota/                quota rules model/service (Redis-backed enforcement)
                              + admin CRUD controller/service/schema (X-Admin-Key)
    analytics/                dashboard/latency aggregation
    shadow_cache/             shadow evaluation model/service/controller
                              (readiness summary + drill-down, CACHE_MODE=shadow)
    health/                   health, live, ready, worker probes
    api_keys/                 API key model/schema/service/controller (tenant identity)
    admin/                    admin API key model/service (scoped operator identity;
                              minted only by app/scripts/create_admin_key.py)
    conversation/             conversation/message model/service/controller (RLS-scoped conversations & chat history)
  worker/
    celery_app.py             Celery app, beat schedule, worker event-loop wiring
  scripts/                    One-off tools (seed, migrate embeddings, test runner)
```

Each feature module follows the same conventions: controllers stay thin, request
and response models live in `*_schema.py`, SQLAlchemy tables live in
`*_model.py`, and business logic lives in `*_service.py`.

## Request lifecycle

The public endpoint is `POST /api/v1/chat/completions`
(`app/modules/chat/chat_controller.py`), guarded by `api_key_auth_optional_guard`
then `llm_quota_guard`. A supplied `X-API-Key` must resolve to an active tenant
key (otherwise `401`); a request with no key proceeds as the `anonymous` tenant,
which is an intentional feature and is subject to the default quota limits. Auth
runs first so a bad key is rejected without consuming rate-limit budget. The
controller fills in identity from the middleware and calls
`LlmRouterService.generate_completion(request, api_key=...)`.

`generate_completion` runs these stages, recording a latency value for each.
Steps 1–3 are gated by `CACHE_MODE` (only `active` reaches them) and, within
`active`, by their `USE_*` flag; see [Shadow cache mode](#shadow-cache-mode):

1. **Redis exact match** — `_get_redis_exact_match`. A versioned SHA-256 cache key
   over every request field that affects the answer (service, model, temperature,
   messages, tools, `response_format`, `stop`, `max_tokens`, penalties, scope IDs).
   Skipped entirely when `USE_REDIS_EXACT_MATCHING` is not `"true"`.
2. **Qdrant exact match** — `_get_qdrant_exact_match`. Persistent secondary exact
   cache. On hit it warms Redis with the remaining TTL. Controlled by
   `USE_QDRANT_EXACT_MATCHING`.
3. **Semantic search** — `evaluate_semantic_cache` (only when
   `USE_QDRANT_SEMANTIC_MATCHING == "true"`). This is the involved part:
   - Generate dense embeddings for the full prompt and dense+sparse embeddings for
     the system and user segments.
   - Apply a Qdrant filter fixing `prompt_version`, `model`, `embedding_model`,
     `embedding_version`, `cache_key_version`, `cacheable`, non-expired
     `expires_at`, plus optional `response_format_hash`, `stop_hash`, and
     `completion_tokens <= max_tokens`, and the cache **scope** hierarchy
     (`GLOBAL`, `TENANT`, `USER`, `SESSION`, `CONVERSATION`).
   - **Fast path:** a single dense query on `prompt_embedding`. If the top score is
     ≥ `QDRANT_DENSE_SEARCH_HIGH_THRESHOLD` (default `0.98`), return immediately and
     warm Redis — no rerank, no intent call (`QDRANT_FAST`).
   - **Hybrid path:** an RRF query over the system/user dense + sparse vectors. Only
     candidates scoring ≥ `QDRANT_SPARSE_SEARCH_HIGH_THRESHOLD` (default `0.33`)
     become eligible.
   - If there are eligible candidates, run **intent classification** (LLM) and
     **entity extraction** (regex), then **cross-encoder reranking**
     (`RerankerService`). The cross-encoder compares the request user segment to
     each candidate's user segment. Its base score is combined with bounded
     signal **factors** (model, service, intent, extracted metadata, entities,
     system prompt, temperature, instruction) — multiplied under the default
     `RERANKER_SCORING_MODE=multiplicative`, so a strong match can never collapse
     to zero through penalty stacking. The decomposition is attached to the top
     candidate as `score_breakdown` and persisted on the trace row. Its `details`
     records, per judged metadata field, the request and candidate values and the
     token overlap that produced the decision (`metadata_comparisons`), so a
     near-boundary rejection can be reconstructed from the trace alone.
     The extracted-metadata mismatch penalty (`0.05`/field, capped at `0.15`) is
     precision-load-bearing: near-duplicate prompts that differ only by one
     attribute (e.g. "check if a **key** exists" vs "…a **value** exists") score
     near-1.0 on the cross-encoder and are rejected *only* by this factor. Do not
     weaken it or the `CACHE_CONFIDENCE_THRESHOLDS` high bounds from an offline
     sweep; tune them from live traces with
     `app/scripts/inspect_semantic_cache.py`.
   - The best candidate is passed to `CacheConfidenceEvaluator.evaluate`, which
     applies per-intent high/moderate thresholds and temperature rules. Accepted
     matches (`QDRANT_SEMANTIC`) are returned and cached in Redis; rejected matches
     become a miss.
   - Requests that declare `tools` bypass the semantic cache entirely so tool
     execution stays deterministic.
4. **Provider call** — `_call_provider_with_retry`. Retryable on timeouts/connection
   errors and HTTP 408/429/500/502/503/504 (tenacity, exponential jitter). On
   failure, if `USE_FALLBACK_LLM == "true"`, retry once against
   `FALLBACK_LLM_SERVICE`/`FALLBACK_LLM_MODEL`. Status 400/401/403/500 raise
   immediately (fatal).
5. **Cache write + logging (async)** — on a real (non-mock) response, cache
   persistence runs in a background `asyncio` task (`_background_save_cache` →
   Redis + Qdrant), plus usage logging (`_log_usage`) and trace logging
   (`_log_gateway_request`). The client is not blocked on any of it.

### Routing decisions recorded

`RoutingDecision` (`app/modules/gateway_requests/gateway_requests_schema.py`):

| Value | Meaning |
| --- | --- |
| `REDIS_EXACT` | Served from Redis exact cache. |
| `QDRANT_EXACT` | Served from the Qdrant exact cache. |
| `QDRANT_FAST` | Served from the dense fast-path threshold. |
| `QDRANT_SEMANTIC` | Served after hybrid search + rerank + confidence gate. |
| `LLM` | Fresh provider response (cache miss). |

### Latency breakdown

`LatencyBreakdown` fields (all milliseconds, best-effort):
`redis_exact_ms`, `qdrant_exact_ms`, `embedding_gen_ms`, `qdrant_dense_ms`,
`qdrant_rrf_ms`, `intent_classify_ms`, `rerank_ms`, `cache_lookup_total_ms`,
`provider_ms`, `cache_write_ms`, `total_ms`.

## Shadow cache mode

`CACHE_MODE=shadow` is the pre-activation mode: operators can validate the
cache against real traffic before letting it answer. The serving path is
provider-only, so the cache contributes **zero latency** and cannot change a
response. All cache work happens in one background task per request.

Gating is centralized in `Config.cache_layer_enabled(layer)` (`redis`,
`qdrant_exact`, `qdrant_semantic`), which returns `False` unless
`CACHE_MODE == "active"`. The lookup and save primitives accept `force=True`,
used only by the shadow evaluator, so it can exercise every layer while nothing
on the serving path can read from the cache.

### Serving path in `shadow` mode

- The Redis exact, Qdrant exact, and semantic lookups are skipped; the request
  goes straight to the provider (`LLM` routing decision, reported as usual).
- No cache write is scheduled on the serving path. The shadow task owns the
  write, which guarantees the corpus is populated even when the replayed lookup
  matched.
- After the response is returned, the router schedules
  `LlmRouterService._background_shadow_evaluate` when
  `_should_run_shadow_evaluation` passes: mode is `shadow`, the response is a
  real (non-mock, non-overflowed) completion, and `SHADOW_SAMPLE_RATE` selects
  the request. The streaming path applies the same rule once the stream
  completes cleanly.
- A shadow failure is logged and swallowed. It can never surface to the client.

### Background evaluator

`_background_shadow_evaluate` replays the full pipeline and records one row in
`shadow_cache_evaluations`:

1. Await the sibling trace-logging task so `request_id` can be filled in when
   it has already finished.
2. Redis exact → Qdrant exact → `evaluate_semantic_cache`, all with
   `force=True`. The semantic stage is the same code the active path runs, so it
   covers the dense fast path, hybrid RRF, intent classification, cross-encoder
   rerank, and the confidence gate. The match type records which layer would
   have answered (`REDIS_EXACT`, `QDRANT_EXACT`, `QDRANT_FAST`,
   `QDRANT_SEMANTIC`) or `MISS`, along with the score inputs and per-stage
   latency breakdown.
3. **Write after lookup.** The order is deliberate: writing first would make the
   request match the entry it just created. The write goes to Redis and Qdrant
   with the same tenant/model/provider-sensitive keying as the active path.
4. Compare the cached text with the fresh answer when a match occurred. Identical
   text counts as equivalent without a judge call; otherwise
   `JudgeService.evaluate_equivalence` (gated by `SHADOW_JUDGE_ENABLED`) returns
   `True`/`False`/`None`, stored in `is_equivalent`.
5. Compute `projected_latency_saved_ms = provider_ms - cache_lookup_ms` for
   would-be hits and persist the row. The background task inherits the request's
   RLS context, so the insert is tenant-scoped like any other write.

### Reading the results

`GET /api/v1/shadow-cache/summary` aggregates a window into would-be hit rate, a
match-type breakdown, judge equivalence, projected latency savings, and
per-intent/per-model hit rates. `ShadowCacheService._evaluate_readiness` turns
those numbers into `READY` / `WARMING_UP` / `NOT_READY` using
`SHADOW_READINESS_MIN_EVALUATIONS`, `MIN_HIT_RATE`, and
`MIN_EQUIVALENCE_RATE`, with reasons for every failed floor. The analytics
dashboard renders that as its "Cache Readiness — Shadow Pipeline" section;
`GET /api/v1/shadow-cache/evaluations` backs drill-down.

When the indicator reports `READY`, set `CACHE_MODE=active` (and enable the
`USE_*` layers you want) and restart.

## Shared inference platform

Dense embeddings, reranking, relevance, and NLI can be served by a separate
deployable (`inference-platform/`, a sibling dependency root inside this repo)
instead of being loaded into the gateway process. One contract, two
implementations: a native FastAPI server for laptops and Infinity for GPU hosts.

```
gateway / doc-nexus / agent-forge
              │  INFERENCE_BASE_URL
              ▼
      inference-platform :7997     ollama :11434
      /embeddings /rerank         (generative: intent, judge)
      /classify /health
```

`INFERENCE_BASE_URL` empty keeps everything in-process (the previous behaviour);
setting it switches `EmbeddingService` and `CrossEncoderService` to HTTP and stops
them importing torch at all. Verified: with the variable set, `torch`,
`transformers`, and `sentence_transformers` are absent from `sys.modules` after
building both services.

### Roles

Requests are routed by **role**, not just by model id:

| Role | Endpoint | Consumer |
| --- | --- | --- |
| `embed` | `/embeddings` | `EmbeddingService` (prompt vectors for the Qdrant cache) |
| `rerank` | `/rerank` | `RerankerService.verify` (candidate rescoring) |
| `relevance` | `/rerank` | `JudgeService` (query-to-answer relevance) |
| `nli` | `/classify` | `JudgeService` (contradiction/entailment) |

Roles are not cosmetic. Two different cross-encoders back `rerank` and
`relevance`; without a role, an unpinned `/rerank` call is served by the engine's
*default* reranker, so relevance would be scored by the wrong model. That
regression was caught end-to-end during the migration (relevance logits differed
by 11.4 before role routing landed). An explicit `INFERENCE_*_MODEL` pin takes
precedence and is required for the Infinity deployment, which routes by model id
only.

### Failure semantics

- **Fail-open on the serving path.** An unreachable engine is a cache miss, not a
  request failure: `evaluate_semantic_cache` catches `InferenceUnavailable`,
  records `mark_cache_degraded("inference_unavailable")` (surfacing as
  `X-Gateway-Degradation: cache_bypassed`), and the request proceeds to the
  provider. `RerankerService.verify` returns no candidates; the judge falls back
  to its defaults. The operator endpoint `POST /api/v1/verifier/predict/rerank`
  returns `503` instead, because there an unavailable engine is a real error.
- **Fail-fast on connection refusal.** `InferenceUnreachable` is not retried:
  retrying a refused connection cannot succeed and would burn the latency budget
  (measured: the whole fail path is ~1-3ms instead of ~1s). Timeouts, throttling,
  and 5xx are retried `INFERENCE_MAX_RETRIES` times.
- **Score scale preserved.** The contract exposes normalized `score` and
  `probabilities`, but the gateway consumes `score_raw` and `logits`, because
  every existing threshold (`reranker` cutoffs, the `(raw + 10) / 4` relevance
  mapping, and the judge's own softmax) was calibrated on raw cross-encoder
  logits. Measured parity with the in-process models is float-exact (max abs diff
  ~5e-7); migrating thresholds onto the normalized scale is a separate change
  that needs recalibration, not a rename.
- **Version skew is loud.** `DependencyContainer.initialize()` probes `/models` and
  warns on an embedding-dimension or model-id mismatch, since a silent model swap
  corrupts the vector space rather than raising an error.

Sparse BM25 is deliberately excluded from the shared contract: `fastembed`'s
sparse vectors are tokenizer-coupled to the points already in Qdrant, so serving
them remotely would risk breaking hybrid search to save ~1ms.

## Degradation contract & resilience architecture

Echo Gate is designed to be *degradable*, not *down*:

1. **Explicit Degradation States**:
   - `FULL` (`X-Gateway-Degradation: none`): Served via cache hit or primary provider completion.
   - `CACHE_DEGRADED` (`X-Gateway-Degradation: cache_bypassed`): Fail-open cache. A Redis or Qdrant failure is treated as a cache miss, proceeding directly to the provider without failing client requests. The fail-open sites (`RedisService.get`, `LlmCacheCollectionService.search_exact` / `query_points_rrf` / `query_points_dense`, and the semantic read in `LlmRouterService.evaluate_semantic_cache`) call `mark_cache_degraded` in `app/core/degradation.py`, so the state reflects what actually happened for that request.
   - `PROVIDER_FALLBACK` (`X-Gateway-Degradation: provider_fallback`): Primary provider failed or its circuit breaker was open; served by fallback provider or subsequent alias target.
   - `HARD_DOWN` (`503 Service Unavailable`): All candidate targets and fallbacks failed, or circuit breaker is open with no remaining targets.
   - The header is set on buffered JSON responses and on SSE streaming responses. For streams the router tags the first chunk before any bytes are written, so `provider_fallback` and `cache_bypassed` are visible even though the body is incremental. `provider_fallback` outranks `cache_bypassed`, and `cache_bypassed` is only reported when the answer actually came from a provider (a cache hit is never labelled bypassed).

2. **Per-Provider Circuit Breakers** (`app/core/circuit_breaker.py`):
   - Keyed per provider (`openai`, `gemini`, `groq`, `openrouter`, `ollama`, `claude`).
   - Tracks consecutive retryable failures (`408`, `429`, `500`, `502`, `503`, `504`, timeouts).
   - When tripped `OPEN`, fast-fails or skips to the next candidate immediately with zero latency penalty.
   - Transitions to `HALF_OPEN` after recovery timeout (`CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS`) to probe upstream recovery.
   - A `HALF_OPEN` breaker admits a single probe; the router releases that permit (`release_probe`) on paths that never reach the provider, such as a shed bulkhead, so a busy gateway cannot strand a recovered provider in `HALF_OPEN` permanently.
   - A provider that dies *after* the first streamed chunk is recorded as a failure too. Success is only counted once a stream completes, and failover is attempted only while no content, reasoning, or tool call has been emitted — after that the client gets a structured SSE error instead of a replayed answer.

3. **Bulkheads & Load Shedding** (`app/core/bulkhead.py`):
   - Global concurrency cap (`GLOBAL_MAX_CONCURRENCY`) and per-provider concurrency caps (`PROVIDER_MAX_CONCURRENCY`).
   - Fast load-shedding with HTTP 429 (`Retry-After`) when bulkheads saturate, preventing runaway latency or resource exhaustion.

4. **Dynamic Model Registry & Model Aliasing** (`app/modules/llm/model_registry.py`):
   - Dynamic model registry supporting runtime addition/removal of models and runtime availability toggles.
   - Model aliases (e.g. `fast`, `cheap`, `quality`) map to ranked lists of `(provider, model)` candidate pairs. Router tries candidates in priority order, skipping any whose breaker is open.

5. **Request Timeout Budget Propagation**:
   - Accepts `X-Timeout-Budget-Ms` header; cuts short slow primary attempts to preserve budget for fallbacks and avoids futile retries.

6. **Quota Budget Advertisement**:
   - `LlmQuotaService.check_quota` returns a `QuotaStatus` (limits, remaining, window resets read from the live Redis TTLs) instead of only raising. The guard sets `X-RateLimit-*` on the injected response, and the chat controller re-applies them on the `StreamingResponse` because a stream replaces that response object.
   - Refusals reuse the same headers: `429` (RPM/TPM) and `503` (provider circuit open / bulkhead shed) carry `Retry-After`, derived from the window TTL or the breaker's remaining recovery time, so downstream clients can pace themselves.
   - When every candidate fails, the terminal `503` also carries `Retry-After`: the longest recovery window among the candidates' breakers (retrying before it expires would hit the same open breakers), else `RETRY_AFTER_DEFAULT_SECONDS`. Non-retryable classifications omit it.

## Cache scoping and TTL

Cache entries carry a `scope` and are only visible to queries at the same level or
narrower, per `SCOPE_HIERARCHY` (`app/core/constants.py`): `GLOBAL(4)` >
`TENANT(3)` > `USER(2)` > `SESSION(1)` > `CONVERSATION(0)`. At write time
`save_to_qdrant_cache` derives the actual scope from whichever identity fields are
present and clamps it down to the entity extractor's maximum allowed scope from
`get_tags_and_max_scope` — so sensitive entities cannot be stored too broadly.

TTL is chosen by `IntentClassifierService.calculate_ttl(intent, time_sensitivity)`:

- `time_sensitivity >= 0.9` or intent `finance_market` → **not cacheable**
  (`None`; the write is stamped with a negative TTL offset and `cacheable=false`).
- Otherwise the value comes from `INTENT_TTL_SECONDS` keyed by intent, defaulting
  to `general_query`. Examples: `weather` 5m, `product_info` 1h, most coding
  intents 24h, `greeting` 7d, `factual_static` 30d.

`CacheConfidenceEvaluator` gates moderate rerank scores: high score always
accepted; moderate accepted only at a middling temperature (0.6–1.6) and only for
subjective intents (`chitchat`, `greeting`); everything else rejected.

## Multi-tenancy (Postgres RLS)

The app connects as a non-owner role (`DB_APP_ROLE`, default `echo_gate_app`), so
Row-Level Security applies to every statement. Per transaction, `app/core/rls.py`
sets three transaction-local GUCs:

- `app.current_tenant` — the acting tenant.
- `app.current_user` — the acting user (optional).
- `app.rls_system` — `'on'` for trusted background jobs that must cross tenants.

Identity resolution order (`TenantContextMiddleware`): `X-API-Key` resolved
server-side to a tenant/user, else the `anonymous` tenant. With no context set,
every policy comparison is `NULL` and no rows are visible (fail-closed). New rows
are stamped with the active tenant/user when the caller leaves them unset. See
[DATA_MODEL.md](DATA_MODEL.md) for the policy shapes per table.

`X-API-Key` is the only source of tenant and user identity, and the only
tenant-scoped credential. There is deliberately no `X-Tenant-ID`/`X-User-ID`
header path: those headers are caller-supplied, so honouring them meant anyone who
could reach the gateway could pick the tenant its request ran as. The request body
cannot supply identity either — `tenant_id`/`user_id` are not body fields, and
`user` in the body is not read for identity or billing. The RLS context therefore
always traces back to one API key row, and keyed callers rate-limit on their key.
Anonymous callers rate-limit on a per-caller fingerprint (hashed client address,
preferring the first `X-Forwarded-For` hop, plus an optional `X-Session-ID`), so
they never share a single bucket; that fingerprint is not an identity and never
reaches the RLS context.

`X-Admin-Key` is deliberately **not** part of that resolution chain: admin keys
are stored apart from tenant keys, so they never become a tenant identity. Admin
routes authenticate with `AdminApiKeyGuard`, which enforces a scope and hands a
resolved identity to the service. Services that need to cross tenants run in
`system_rls_context()`, which disables RLS for the transaction — so
`LlmQuotaRuleService` refuses to open a session without a resolved
`AdminKeyIdentity`, turning a forgotten or mis-wired guard into a loud error
rather than a silent bypass.

## Background workers (Celery)

`app/worker/celery_app.py` includes task modules from `llm_usage`,
`gateway_requests`, and `qdrant`. Each prefork child initializes its own event loop
and the `DependencyContainer` once (`worker_process_init`), because the async
clients are loop-bound; `run_async` runs task coroutines on that loop.

Registered Beat tasks:

| Task | Schedule (as coded) | Purpose |
| --- | --- | --- |
| `app.modules.llm_usage.tasks.process_uncalculated_costs` | every 5 min | Fill in `cost` for usage logs where `cost_calculated` is false. |
| `app.modules.gateway_requests.tasks.evaluate_pending_requests` | every 1 min | Evaluate `PENDING` traces with the judge/cross-encoder to flag false-positive cache hits. |
| `delete_expired_vectors` (qdrant) | every 5 min | Delete expired points from the Qdrant cache collection. |

`deploy/compose/docker-compose.yml` starts both a `celery_worker` and a `celery_beat` service,
so scheduled tasks run under `docker compose -f deploy/compose/docker-compose.yml up`.

## Health and observability

- `GET /` returns app metadata.
- `GET /api/v1/health` — basic healthy/version.
- `GET /api/v1/health/live` — process liveness.
- `GET /api/v1/health/ready` — **gating**. Postgres only (1s timeout; 503 on
  failure). See the tiering note below.
- `GET /api/v1/health/deps` — **non-gating** roll-up for alerting: Postgres,
  Redis, Qdrant, Celery broker and workers. Always 200; read the `status` field
  (`healthy` / `degraded` / `unhealthy`).
- `GET /api/v1/health/worker` and `/api/v1/health/celery` — Celery broker + workers.

### Probe tiering

The probes are tiered by *what breaks*, not by *what is configured*. `/ready`
gates on Postgres alone because that is the only dependency whose failure makes
the gateway answer **incorrectly** rather than merely slower:

- **Redis** — `LlmQuotaService.check_quota` catches the failure and falls back to
  an unlimited budget; cache reads are wrapped so a miss becomes a provider call.
- **Qdrant** — a failed lookup is logged and treated as a cache miss.
- **Postgres** — `get_applicable_limits` reads quota rules through `SessionLocal`
  with no handler, so the error escapes as a 500 on every chat request; and
  `resolve_api_key` swallows the database error and returns `None`, which
  `ApiKeyAuthGuard` reports as a 401 for a key that is in fact valid.

Redis and Qdrant stay on `/deps` so a cache blip is still visible to an operator
without evicting every replica and converting graceful degradation into an
outage.
- Every response carries `X-Correlation-ID` and `X-Request-ID`; the chat response
  also exposes `X-Cache`.
- Structured access logs include method, path, status, duration, and client IP.

## Known gaps and discrepancies

These are real today; documented rather than silently changed.

1. **Analytics page is read-only.** The Next.js analytics page renders tables and
   cards rather than charts (no charting library is installed).

Former gaps (conversation router unmounted, README model drift, anonymous
shared rate-limit bucket, `.env.example` key drift, scheduled-task mismatch)
have been resolved; the record lives in `docs/ROADMAP.md`.
