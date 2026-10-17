# Architecture

Echo Gate is a FastAPI service that presents an OpenAI-compatible
`chat/completions` API in front of several LLM providers, with a multi-level
semantic cache in front of the providers. A separate Next.js app in `ui/` renders
the chat playground, trace explorer, and analytics dashboards.

## System components

| Component | Where | Role |
| --- | --- | --- |
| HTTP API | `app/main.py`, `app/modules/*/*_controller.py` | FastAPI app, middleware, routers. |
| Routing/cache core | `app/modules/llm/llm_router_service.py` | Owns the request lifecycle: exact cache, semantic cache, verification, provider call, cache write, logging. |
| Provider adapters | `app/modules/llm/llm_provider_service.py` | One method per provider: `google-genai`/`gemini`, `openai`, `groq`, `openrouter`, `ollama`. |
| Embeddings | `app/modules/embedding/embedding_service.py` | Dense embeddings (`sentence-transformers`) and sparse lexical embeddings (BM25). |
| Redis cache | `app/modules/redis/redis_service.py` | Exact-match cache and quota counters. |
| Qdrant cache | `app/modules/qdrant/llm_cache_collection_service.py` | Vector cache collection `embeddings_multivector`: upsert, exact search, dense search, hybrid RRF search, expiry deletion. |
| Intent + entities | `app/modules/intent_classifier/` | LLM-based intent classification and regex-based entity extraction; scope/TTL decisions. |
| Reranking/verification | `app/modules/reranking/reranker_service.py`, `app/modules/llm/cache_confidence.py`, `app/modules/verifier/` | Cross-encoder rerank, confidence gate, and the async judge/cross-encoder evaluators. |
| Relational store | `app/core/database.py`, `app/modules/*/*_model.py`, `alembic/` | SQLAlchemy models on PostgreSQL with Row-Level Security. |
| Background workers | `app/worker/celery_app.py`, `*/tasks.py` | Celery worker + Beat: cache eviction, cost calculation, pending-request evaluation. |
| UI | `ui/` | Next.js 16 + React 19 observability dashboard. |
| DI container | `app/core/dependencies.py` | Builds every singleton service and tears them down on shutdown. |

## Module layout

```text
app/
  main.py                     FastAPI app, lifespan, middleware, router registration
  core/
    config.py                 Environment-backed settings (single Config instance)
    dependencies.py           DependencyContainer: constructs/shares services
    database.py               SQLAlchemy engines/sessions (read + write)
    rls.py                    Postgres RLS GUC context (tenant/user/system)
    middlewares.py            CorrelationId, User, TenantContext middleware
    guards.py                 llm_quota_guard (RPM/TPM/budget enforcement)
    retry.py                  tenacity-based retry (status 408/429/5xx + timeouts)
    responses.py              BaseAPIResponse envelope
    pricing.py                Per-provider/model token pricing
    constants.py              Version tags, scope hierarchy, prefixes
    logger.py                 Structured/colored logging + correlation IDs
  modules/
    chat/                     chat schema + controller (the public completions API)
    llm/                      provider adapters, router service, prompts, confidence
    embedding/                dense + sparse embedding service
    redis/                    Redis client wrapper
    qdrant/                   Qdrant client, cache collection service, controller, schema, tasks
    intent_classifier/        intent LLM service, regex entity extractor, intent schema
    reranking/                cross-encoder reranker
    verifier/                 cross-encoder + judge + instruction verifier, controller
    gateway_requests/         trace model/schema/service/controller + evaluation task
    llm_usage/                usage model/schema/service/controller + cost task
    llm_quota/                quota rules model/service (Redis-backed enforcement)
    analytics/                dashboard/latency aggregation
    health/                   health, live, ready, worker probes
    api_keys/                 API key model/schema/service/controller (tenant identity)
    conversation/             conversation/message model/service/controller (NOT registered — see gaps)
  worker/
    celery_app.py             Celery app, beat schedule, worker event-loop wiring
  scripts/                    One-off tools (seed, migrate embeddings, test runner)
```

Each feature module follows the same conventions: controllers stay thin, request
and response models live in `*_schema.py`, SQLAlchemy tables live in
`*_model.py`, and business logic lives in `*_service.py`.

## Request lifecycle

The public endpoint is `POST /api/v1/chat/completions`
(`app/modules/chat/chat_controller.py`), guarded by `llm_quota_guard`. It fills in
identity from the middleware and calls
`LlmRouterService.generate_completion(request, api_key=...)`.

`generate_completion` runs these stages, recording a latency value for each:

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
     candidate as `score_breakdown` and persisted on the trace row.
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
server-side to a tenant/user, else trusted `X-Tenant-ID`/`X-User-ID` headers, else
the `anonymous` tenant. With no context set, every policy comparison is `NULL` and
no rows are visible (fail-closed). New rows are stamped with the active
tenant/user when the caller leaves them unset. See
[DATA_MODEL.md](DATA_MODEL.md) for the policy shapes per table.

## Background workers (Celery)

`app/worker/celery_app.py` includes task modules from `llm_usage`,
`gateway_requests`, and `qdrant`. Each prefork child initializes its own event loop
and the `DependencyContainer` once (`worker_process_init`), because the async
clients are loop-bound; `run_async` runs task coroutines on that loop.

Registered Beat tasks:

| Task | Schedule (as coded) | Purpose |
| --- | --- | --- |
| `app.modules.llm_usage.tasks.process_uncalculated_costs` | every 30s | Fill in `cost` for usage logs where `cost_calculated` is false. |
| `app.modules.gateway_requests.tasks.evaluate_pending_requests` | every 30s | Evaluate `PENDING` traces with the judge/cross-encoder to flag false-positive cache hits. |
| `delete_expired_vectors` (qdrant) | every 30s | Delete expired points from the Qdrant cache collection. |

> The comments next to each entry describe longer intervals ("every 5 minutes",
> "every 1 minute"), but the actual coded `schedule` is `30.0` seconds for all
> three. Treat the numeric value as authoritative.

`docker-compose.yml` starts a `celery_worker` service but **not** a Beat service,
so scheduled tasks do not run under plain `docker-compose up` — start Beat
separately (`celery -A app.worker.celery_app beat`).

## Health and observability

- `GET /` returns app metadata.
- `GET /api/v1/health` — basic healthy/version.
- `GET /api/v1/health/live` — process liveness.
- `GET /api/v1/health/ready` — Postgres + Redis + Qdrant (1s timeouts; 503 on failure).
- `GET /api/v1/health/worker` and `/api/v1/health/celery` — Celery broker + workers.
- Every response carries `X-Correlation-ID` and `X-Request-ID`; the chat response
  also exposes `X-Cache`.
- Structured access logs include method, path, status, duration, and client IP.

## Known gaps and discrepancies

These are real today; documented rather than silently changed.

1. **Conversations are unreachable.** `app/modules/conversation/` defines
   `APIRouter(prefix="/api/v1/conversations")`, but `app/main.py` never includes it,
   so every conversation route returns 404. The models/tables and RLS policies
   exist.
2. **Streaming implemented.** `ChatCompletionRequest.stream=True` is supported via Server-Sent Events (`text/event-stream`), backed by provider streams, exact/semantic cache playback, and guarded in-memory response accumulation.
3. **UI gaps.** The Next.js app hardcodes `http://localhost:8000` (no env var), the
   sidebar links to `/cache` and `/settings` which have no routes (404), and the
   analytics page is read-only tables/cards rather than charts.
4. **`.env.example` drift.** `EMBEDDING_DIMENSION`, `EMBEDDING_DEVICE`, and
   `CROSS_ENCODER_VERIFIER_MODEL` appear in `app/.env.example` but are read nowhere;
   meanwhile the relevance cross-encoder is configured by
   `CROSS_ENCODER_RELEVANCE_MODEL`, which is not in the example. See
   [CONFIGURATION.md](CONFIGURATION.md#not-read-by-anything).
5. **README model drift.** The README mentions `deepseek-r1` and
   `nomic-embed-text`; the enforced allowlist is `ALLOWED_MODELS` in
   `app/modules/chat/chat_schema.py`, and the default embedding model is
   `all-MiniLM-L6-v2` (the embedding service is constructed with `use_ollama=False`).
6. **Celery Beat interval comments** disagree with the coded schedules (see above).
