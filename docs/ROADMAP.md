# Echo Gate Roadmap

This document is the single history and forward plan for Echo Gate. It absorbs
the former root-level `plans.md` and `evaluation_pipeline_plans.md` so they no
longer need to be tracked separately. It has two halves:

- **Landed** — features already developed and shipped, recorded here so the
  context is not lost.
- **Planned** — approved architectural work in suggested development order.

An intentional decision (2026-10-19): **Echo Gate will not ship its own MCP
endpoint.** The agentic/IDE surface will be provided by an external,
contract-driven MCP server project instead. This removes the former "MCP
Server" plan section; revisit natively only if that project cannot cover the
use case.

Interim (2026-10-21): while the external MCP server is still early (it cannot
yet compile OpenAPI contracts or serve MCP transports), a **local-only
FastMCP bridge** lives in `mcp-bridge/` (own venv, outside `app/`, excluded
from Docker and the OpenAPI snapshot) so `agent-forge` can consume the gateway
over MCP today. The decision above still stands — `mcp-bridge/` is throwaway
dev tooling to be deleted once the external server covers the use case.

Adopted capabilities that already exist are listed under [Landed](#landed);
everything else below is future work.

---

## Landed

Feature-by-feature record of what exists in the codebase today.

### Gateway core

- [x] **OpenAI-compatible completions API** (`POST /api/v1/chat/completions`):
  provider routing across Gemini (`google-genai`), OpenAI, Groq, OpenRouter,
  Ollama, Claude, plus a mock provider for tests.
- [x] **Anthropic Messages Protocol Adapter** (`POST /api/v1/messages` and `/v1/messages`, `2026-11-07`):
  native wire protocol compatibility for Anthropic SDK clients (Claude Code, Cursor)
  with bidirectional schema normalization, streaming SSE events translation, and cache hit playback.
- [x] **Per-Request Gateway Directives** (`2026-11-07`):
  request-level overrides for `cache_enabled` (bypass / force refresh), `cache_ttl`
  (custom TTL in seconds), and `retries` bounds passed to upstream providers.
- [x] **Natural Streaming Playback** (`2026-11-07`):
  cache-hit stream playback with realistic word/token chunking and micro-interval
  pacing (`STREAM_CACHE_PACING_DELAY_MS`) to eliminate UI flickering and support typewriter rendering.
- [x] **Conversations API** (`2026-10-21`): the previously unmounted
  `conversation/` router is live in `app/main.py` —
  `POST/GET /api/v1/conversations`, `GET /{id}`,
  `POST /{id}/chat` (history-aware completion that persists user and
  assistant messages). RLS-scoped via the standard identity path; OpenAPI
  snapshot regenerated (purely additive).
- [x] **Full streaming support** (`stream: true`): SSE responses with OpenAI-style
  chunks (`data: {...}` … `data: [DONE]`), per-provider stream adapters
  (OpenAI/Groq/OpenRouter share `_stream_openai_compatible`, Ollama
  line-delimited JSON, Gemini `generate_content_stream`, Claude SSE parser,
  mock); guarded in-memory accumulation (`StreamAccumulator` with a
  `STREAM_MAX_CONTENT_BYTES` guardrail), fallback-switchover before/after the
  first chunk, cache-hit SSE playback from cached completions, background cache
  writes only on clean `finish_reason` completion, and mid-stream error
  surfaced as an SSE error event. Covered by `tests/integration/test_streaming_support.py`
  (31 tests). (The original `streaming_plan.md` was completed and deleted.)
- [x] **Provider resilience**: retry with `RETRYABLE_STATUS_CODES`
  (`app/core/retry.py`) and primary→fallback provider switchover, both on the
  blocking and streaming paths.
- [x] **Tool-call support**: `tools`/`tool_choice` passthrough; requests declaring
  `tools` bypass the semantic cache for deterministic execution; tool-call
  chunk assembly in streaming.

### Semantic cache pipeline

- [x] **Multi-level lookup**: Redis exact → Qdrant exact → Qdrant semantic
  (dense fast-path, else hybrid dense/RRF fusion → intent classification →
  cross-encoder rerank → `CacheConfidenceEvaluator` acceptance gate).
- [x] **Tenant/model/provider-sensitive keying**: SHA-256 exact key over all
  answer-affecting fields; Qdrant payload filters on `prompt_version`,
  `model`, `embedding_version`, `cache_key_version`, `cacheable`,
  `expires_at`, and cache scope. Time-sensitive queries and certain finance
  intents are never cacheable.
- [x] **Cache scope**: entity extraction widens storage scope only as far as
  allowed; sensitive prompts are never stored `GLOBAL`.

### Multi-tenancy and identity

- [x] **Postgres RLS**: app connects as a non-owner role; per-tenant policies on
  all tenant-scoped tables; trusted background jobs use the system RLS
  context. Default context is fail-closed (no cross-tenant visibility).
- [x] **API-key identity**: `X-API-Key` resolves to tenant/user via
  `TenantContextMiddleware`; strict mode for tenant-scoped reads, optional
  mode for anonymous chat. No caller-supplied `X-Tenant-ID`/`X-User-ID` trust
  path.
- [x] **Admin surface**: scoped `X-Admin-Key` guard (`AdminApiKeyGuard`,
  `admin_api_keys` table), keys minted only by `app/scripts/create_admin_key`.
  Quota-rule CRUD and effective-limits resolution under `/api/v1/admin/*`.

### Quota and rate limiting

- [x] **Per-caller Redis counters** for RPM/TPM plus a monetary `budget:` check;
  rule-driven limits via `llm_quota` (admin CRUD covered in the UI).
- [x] **Per-caller anonymous isolation**: keyless callers rate-limit on a hashed
  per-caller fingerprint (first `X-Forwarded-For` hop / socket address,
  optionally split by `X-Session-ID`) instead of one shared anonymous bucket.
  (`test_anonymous_rate_limit.py`; replaced the former shared
  `rpm:None:*` DoS vector.)

### Background jobs

- [x] **Celery worker + beat** (both started under `docker-compose`): per-child
  event loop with loop-bound `DependencyContainer` clients; beat schedule of
  5-min usage-cost backfill, 1-min pending-request evaluation, 5-min expired
  vector cleanup. In-request background work uses
  `_create_background_task` (guarded fire-and-forget) for cache writes and
  usage/trace logging.

### Observability and APIs

- [x] **Health surfaces**: `/`, `/api/v1/health`, `/live` (process), `/ready`
  (gating, Postgres), `/health/deps` (non-gating roll-up),
  `/health/worker`, `/health/celery`.
- [x] **Structured logging** with correlation IDs, latency breakdowns
  (`redis_exact_ms`, `qdrant_exact_ms`, `provider_ms`, `cache_write_ms`,
  `total_ms`), `X-Cache` header, trace/usage logging (gateway_requests,
  llm_usage) with judge evaluation status fields.
- [x] **Analytics endpoints** (dashboard aggregates, model breakdown, latency).
- [x] **Test/debug endpoints**: semantic-search, exact-search, cache-save.
  Re-scoped *(2026-11-04)*: the reads need an admin key with
  `cache:read` and take an explicit `?tenant_id=`; `cache-save` needs
  `cache:write`. Tenant and anonymous keys were previously accepted
  (two routes had no auth at all), which exposed and let callers write
  the shared anonymous cache scope.

### ML components

- [x] **Dense embeddings & BM25 sparse**: dense embeddings
  (`BAAI/bge-small-en-v1.5`, 384-dim, served by `inference-platform/`), BM25
  sparse embeddings with a persisted model path, cross-encoder relevance
  (`ms-marco-MiniLM-L-6-v2`), rerank (`gte-reranker-modernbert-base`), NLI
  (`nli-MiniLM2-L6-H768`) and zero-shot classification
  (`ModernBERT-base-zeroshot-v2.0`), LLM judge (`llm_usage`-fed evaluation),
  intent classifier, core-query entity extractor. Every default is the
  small-but-capable pick for a 16 GB laptop; the production swap for each role is
  recorded in `inference-platform/models.lock.yaml` and in the model table below.
- [x] **Reranker threshold calibration script** (`app/scripts/calibrate_reranker.py`)
  with precision/recall/F1 sweep.

### UI (`web/` — Next.js 16, App Router, React 19, Tailwind v4, Radix)

- [x] **Chat playground** (provider/model pickers, temperature/max-tokens,
  structured-output modes, API key, cache-status badges, `?hash=` trace
  reload), traces table + trace detail (latency waterfall, intent metadata,
  evaluation scores), analytics dashboard, admin quota rule management with
  `X-Admin-Key` auth, settings page.
- [x] **Backend URL configurable** via `NEXT_PUBLIC_API_BASE_URL`
  (`web/src/lib/api.ts`) with a `web/.env.example`.

### Ops and repo hygiene

- [x] **docker-compose.yml** with qdrant/redis/postgres/api/worker/beat;
  `.dockerignore` keeping secrets and dev tooling out of images.
- [x] **Alembic migrations** with a non-owner app role + owner-role
  `MIGRATION_DATABASE_URL`; OpenAPI snapshot contract test
  (`tests/contract/test_openapi_snapshot.py`); ruff + ruff-format + prettier/eslint
  pre-commit hooks.
- [x] **OSS launch packaging** (`2026-10-21`): AGPL-3.0 `LICENSE`, `NOTICE`,
  README safe-harbor; `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`,
  `CHANGELOG.md`; GitHub Actions (backend + UI CI, Docker buildx/Trivy,
  release-drafter) and issue templates; general `.env` ignore patterns,
  qdrant one-offs under `app/scripts/`.
- [x] **Test suite & documentation set**: ~512-test suite (495 passing today; 17
  Postgres-dependent tests skip without a reachable database), co-located under
  `api/app/modules/*/tests/` plus `tests/{integration,contract}/`;
  documentation set in `docs/` (architecture, API reference, configuration,
  data model, development).

---

## Planned

Suggested development order. Earlier phases keep the core product safe and
reviewable; later phases add the commercial surface. Every phase is
independently shippable and checkable.

### Phase 0 — OSS launch hygiene (unblocks everything else)

Small diffs, no behavior change. Pre-req for opening the repo publicly.

**Status: ✅ 100% complete as of 2026-10-21**

- [x] **1. Licensing**
  - [x] Adopt **AGPL-3.0** as primary copyleft open-source license.
  - [x] Canonical `LICENSE` file containing full AGPL-3.0 legal text.
  - [x] `pyproject.toml` license field set to `AGPL-3.0-only`.
  - [x] `NOTICE` file specifying copyright attribution and dependency notes.
  - [x] README license badge and self-host safe-harbor section.
  - [x] `CONTRIBUTING.md` terms referencing AGPL-3.0-only inbound/outbound.
  - [x] Commercial license (`LICENSE.commercial`) & CLA workflow intentionally deferred to Phase 4.

  | License | How it feels | Cloud-competitor protection | Notes |
  | --- | --- | --- | --- |
  | `AGPL-3.0` | true OSS | Strong (network-use clause) | Strongest copyleft; needs legal review before any acquisition/licensing event. |
  | `Apache-2.0` / `MIT` | Most adoption | None | Max contributor friendliness; others could SaaS it. |
  | `FSL-1.1-MIT` (Sentry style) | OSS in 2 years | Strong, time-based | Freestanding and recent; understandable. |
  | `BSL-1.1` | source-available | Strong | Not OSI "open source"; some devs will bounce. |

- [x] **2. Meta files & CI/CD**
  - [x] `CONTRIBUTING.md` contributor guidelines and PR conventions.
  - [x] `SECURITY.md` (GitHub Private Vulnerability Reporting + security-relevant invariants).
  - [x] `SUPPORT.md` channels and troubleshooting guidance.
  - [x] `CHANGELOG.md` (Keep-a-Changelog specification, 0.1.0 initial release entry).
  - [x] `.github/workflows/ci.yml` (backend: compileall + ruff + pytest incl. offline OpenAPI snapshot; UI: Node 22 lint + build).
  - [x] `.github/workflows/docker.yml` (PR: amd64 build + Trivy SARIF; main: multi-arch buildx push to GHCR + failing Trivy gate).
  - [x] `.github/workflows/release.yml` and `.github/release-drafter.yml`.
  - [x] GitHub issue templates (`.github/ISSUE_TEMPLATE/` bug report, feature request, and security routing configuration).

- [x] **3. Codebase hygiene & documentation cleanup**
  - [x] Mount previously unmounted `conversation/` router in `app/main.py`.
  - [x] Regenerate OpenAPI snapshot contract (`tests/contract/snapshots/openapi.json`) for conversation routes.
  - [x] README quickstart with real architecture diagram and working setup steps.
  - [x] Working admin key workflow documented in README quickstart.
  - [x] High-resolution UI screenshots captured and embedded into `README.md` (`docs/assets/screenshots/`).
  - [x] Audit code, `.env.example`, and `README.md` for internal names/leaks; fix hardcoded paths in `app/scripts/migrate_embeddings.py`.
  - [x] Consolidate one-off root scripts under `app/scripts/` (`python -m app.scripts.*`).
  - [x] Ignore local `.env` variants with explicit `.env.example` negation in `.gitignore`.
  - [x] Clean up stale artifacts (`run_test_results.csv` deleted and ignored).

- [x] **4. Repository restructure**
  - [x] Restructure backend gateway code: `app/` → `api/app/` with Poetry package root mapping (`packages = [{include = "app", from = "api"}]`) and test `pythonpath = [".", "api"]`.
  - [x] Restructure frontend application: `ui/` → `web/` with updated Node scripts, CI steps, and documentation.
  - [x] Relocate Compose definitions: root `docker-compose.yml` → `deploy/compose/docker-compose.yml` with updated relative build contexts (`../..`).
  - [x] Update CI/CD workflows, Dockerfiles, `.dockerignore`, `alembic.ini`, and dev runners (`dev.sh`) to reflect the clean monorepo layout.

---

### Phase 1 — Provider resilience & single-point-of-failure hardening

Echo Gate sits in-line for every service and agent that calls it, so its
failure mode must be *degraded*, not *down*. The app tier is already stateless
(all state lives in Postgres/Redis/Qdrant), which makes horizontal scaling the
primary mitigation, but the following gaps stand between the fail-open cache
design and a genuinely resilient gateway:

**Status: ✅ 100% complete as of 2026-10-22**

- [x] **Tiered health probes**: keep `/live` process-only and `/ready` minimal;
  ensure a cache blip never evicts gateways from load-balancer membership
  (the `/health/deps` roll-up stays non-gating, used for alerting only). Root
  level `/live`, `/ready`, `/health/deps` aliases mapped.
- [x] **Per-provider circuit breaker**: added open/half-open/closed state keyed by
  provider in `app/core/circuit_breaker.py`, tripping on consecutive retryable
  failures from `RETRYABLE_STATUS_CODES` in `app/core/retry.py`. Fast-fails
  or skips open providers with zero latency penalty. A half-open probe permit is
  released when the attempt never reaches the provider (shed bulkhead, budget
  abort), so a busy gateway cannot strand a recovered provider in `HALF_OPEN`.
- [x] **Bulkheads and load shedding**: global concurrency cap (`GLOBAL_MAX_CONCURRENCY`)
  plus per-provider concurrency caps (`PROVIDER_MAX_CONCURRENCY`) in
  `app/core/bulkhead.py`; sheds excess load with `429 Too Many Requests`.
- [x] **Explicit degradation contract**: formalized states — full (`none`) →
  cache-degraded (`cache_bypassed`) → provider-fallback (`provider_fallback`) →
  hard-down `503`, exposed in `X-Gateway-Degradation` response headers on both
  JSON and SSE responses and documented in `docs/ARCHITECTURE.md`. Cache read
  failures are marked fail-open at the source (`app/core/degradation.py`) rather
  than inferred by the router.
- [x] **Resilient client guidance**: documented client retry strategies,
  exponential backoff with jitter, budget propagation, and emergency bypass in
  `docs/RESILIENT_CLIENT_GUIDANCE.md`.
- [x] **Config & docs**: added all new knobs to `app/core/config.py` and
  `docs/CONFIGURATION.md`.
- [x] **Dynamic model registry**: config-driven dynamic registry in
  `app/modules/llm/model_registry.py` with runtime registration, unregistration,
  and availability toggling (`ALLOWED_MODELS_JSON`).
- [x] **Model aliasing / intelligent routing**: model aliases like `fast`, `cheap`,
  `quality` map to ranked lists of `(provider, model)` candidate pairs; router tries
  candidates in priority order, skipping open circuit breakers or saturated bulkheads.
- [x] **Request timeout budget propagation**: accepts `X-Timeout-Budget-Ms` header;
  dynamically allocates timeout across primary, retries, and fallback, cutting short
  slow attempts before deadline exhaustion.
- [x] **Structured provider error passthrough**: normalized error classification
  in `app/core/error_classification.py` (`provider_content_filter`,
  `provider_rate_limit`, `provider_quota_exceeded`, `provider_unavailable`,
  `provider_auth_error`, `provider_timeout`), surfaced in error payloads.
- [x] **API key rotation / expiry**: added `expires_at` column to `api_keys` with
  Alembic migration `c3d4e5f6a7b8`; added `POST /api/v1/admin/api-keys/{id}/rotate`
  action creating a replacement key while expiring the old key after grace period.

---

### Phase 1.5 — Shadow cache pipeline (production onboarding)

A new `CACHE_MODE` setting (`disabled | shadow | active`) that lets operators
run the full cache pipeline in observation-only mode before trusting it to
serve responses. Designed for initial production adoption: deploy with
`CACHE_MODE=shadow`, let real traffic build the cache and accumulate matching
telemetry, validate the numbers, then flip to `active` and restart.

**Status: ✅ 100% complete as of 2026-10-26**

In `shadow` mode:

1. Every request goes straight to the LLM provider (zero cache latency
   impact on the serving path).
2. After the response is returned, a background task runs the full cache
   pipeline asynchronously: generate embeddings → Redis exact lookup →
   Qdrant exact lookup → dense fast-path → hybrid RRF + intent + rerank →
   confidence gate.
3. Results are logged to a `shadow_cache_evaluations` table: whether a match
   would have occurred, the match type (`REDIS_EXACT`, `QDRANT_EXACT`,
   `QDRANT_FAST`, `QDRANT_SEMANTIC`, `MISS`), scores, intent, latency
   breakdown, the cached response text (if any), and the actual LLM response
   for comparison.
4. The embeddings + response are stored in the cache (Redis exact + Qdrant)
   regardless — building up the cache corpus for when it is activated.
5. When a shadow match is found, a lightweight judge comparison (cached vs
   actual) pre-validates cache quality and feeds the Phase 2 audit table.

This gives operators a dashboard view before enabling the cache:

- "X% of requests would have been cache hits"
- "Of those, Y% would have returned equivalent responses"
- "Average latency that would have been saved: Z ms"
- Intent distribution, false positive rate, per-model hit rates.

When the numbers show the cache is trustworthy → `CACHE_MODE=active` →
restart → done. The `shadow_cache_evaluations` data also seeds Phase 2's
conformal threshold calibration.

- [x] **`CACHE_MODE` config**: add `CACHE_MODE` (`disabled` / `shadow` /
  `active`, default `disabled`) to `app/core/config.py`. `disabled` is the
  current default behavior (no cache). `active` is the current `true`
  behavior of the three `USE_*` flags. `shadow` is the new mode. In `active`
  mode the three granular `USE_*` flags still control which layers are on; in
  `shadow` mode all layers run in the background regardless.
- [x] **Shadow evaluation background task**: after provider response, enqueue
  a Celery task (or `_create_background_task`) that runs the full cache
  lookup pipeline and logs the result. Must not block the client response.
- [x] **`shadow_cache_evaluations` table + migration**: Postgres table with
  `request_id` (FK → `gateway_requests`), `tenant_id`, `model`,
  `would_have_hit` (bool), `match_type` (varchar), `match_score` (numeric),
  `rerank_score` (numeric), `score_breakdown` (json),
  `cached_response_text`, `actual_response_text`, `is_equivalent` (bool,
  nullable — populated when judge runs), `intent`, `entities`,
  `latency_breakdown` (json), `created_at`. RLS-scoped by tenant.
- [x] **Shadow cache writes**: in shadow mode, always write embeddings +
  response to Qdrant and Redis after the provider call, building the cache
  corpus passively.
- [x] **Shadow analytics dashboard**: add shadow hit rate, equivalence rate,
  and projected latency savings to the analytics dashboard and `web/`. Show a
  "cache readiness" indicator that summarizes whether the metrics support
  switching to `active` mode.
- [x] **Config & docs**: document `CACHE_MODE` in `docs/CONFIGURATION.md`.
  Note that `shadow` supersedes the three `USE_*` flags (which remain for
  granular control in `active` mode).

Implementation notes: the resolution rule is explicit `CACHE_MODE` → legacy
`USE_*` derivation, so pre-existing deployments keep behaving the same. Serving
gates go through `Config.cache_layer_enabled(layer)` and are bypassed only by the
shadow evaluator (`force=True`). Readiness is served by
`GET /api/v1/shadow-cache/summary` (with `GET /api/v1/shadow-cache/evaluations`
for drill-down) rather than being folded into the analytics dashboard response,
keeping the dashboard contract unchanged. Cost is bounded by
`SHADOW_SAMPLE_RATE`, `SHADOW_JUDGE_ENABLED`, and the three
`SHADOW_READINESS_*` floors.


---

### Phase 2 — Cache-quality loop: shadow audit, eviction, calibration

Core principles (from the evaluation pipeline plan):

1. **Zero serving-path latency**: evaluation runs completely out-of-band in
   background tasks; the client receives the cached response immediately.
2. **Avoid the ratchet trap**: telemetry must not rely exclusively on cache
   misses/rejections (selection bias). Run a low-rate (~1–2%) random audit on
   *approved cache hits* to capture unbiased ground truth.
3. **Keep vector storage stateless**: no statistical arrays or logistic
   regression inside Qdrant point payloads. Postgres is the source of truth
   for evaluation history; Qdrant stays an index.

Partially implemented today: the LLM judge (`app/modules/verifier/judge_service.py`),
the `PENDING → EVALUATED` trace pipeline (`gateway_requests`), and the periodic
`evaluate_pending_requests` Celery task are live. The remaining-work list below
is now closed: sampled fresh-answer audits, comparative judging, instant
eviction, conformal calibration, and dataset export all landed (2026-11-04).

- [x] **Asynchronous shadow audit trigger** (`LlmRouterService`): on a semantic
  cache hit, roll a small sample probability (p ≈ 0.01–0.02); when selected,
  return the cached response immediately and enqueue a background task that
  (1) requests a fresh completion from the provider for the same prompt,
  (2) dispatches cached-vs-fresh to the judge, (3) persists the result to a
  dedicated audit table.
- [x] **Comparative lightweight judge**: extend the judge with the zero-shot
  equivalence prompt (respond `YES`/`NO` for semantic equivalence, run on a
  fast low-cost model so audit overhead stays ~0.01–0.05% of total LLM
  spend), so the judge can catch silent false-positive hits, not just score
  cached answers in isolation.
- [x] **Audit ground-truth store**: a dedicated Postgres table
  (`cache_evaluations`): `request_id`, `tenant_id`, `model_name`,
  `query_text`, `cached_answer`, `fresh_answer`, `intent`,
  `vector_similarity`, `rerank_score`, `is_equivalent`, `cache_point_id`,
  `redis_cache_key`, `latency_saved_ms`, `created_at`. Alembic migration via
  the standard flow.
- [x] **Instant eviction (kill switch)**: when an audited hit is judged
  non-equivalent, immediately delete the Qdrant point
  (`llm_cache_service.delete_point`) and the Redis key so no subsequent
  request can hit the poisoned/stale entry.
- [x] **Periodic intent threshold calibration (conformal risk control)**: a
  scheduled batch job (or admin CLI) analyzes the audit table grouped by
  intent and computes an empirical threshold quantile guaranteeing an error
  rate ≤ α (e.g. ≤ 2%) per intent, applying it as a Redis override
  (`cache:thresholds:{intent}`) rather than online mutation. The scheduled job
  and `python -m app.scripts.calibrate_intent_thresholds` both read live
  `cache_evaluations` rows; `app/scripts/calibrate_reranker.py --source audit`
  sweeps the same ground truth directly (precision/recall/F1 plus a per-intent
  conformal recommendation) with no inference engine and no static dataset, and
  the default `--source static` replay of `TEST_DATASET` remains for offline
  reranker tuning.

- [x] **In-domain dataset export**: export `(query_text, cached_answer, is_equivalent)`
  triples as they accumulate to fine-tune an in-domain cross-encoder reranker
  (`CacheAuditService.export_in_domain_dataset` +
  `python -m app.scripts.export_cache_audit_dataset`, JSONL/JSON/CSV).
- [x] **Cache warmup / pre-seeding API** *(2026-11-04)*: admin-scoped
  `POST /api/v1/admin/cache/warmup` accepting a batch of `(request, response)`
  pairs, written in parallel via the existing `_background_save_cache` logic.
  Useful for seeding new environments from production traces, restoring cache
  after a Qdrant reindex, or preloading high-traffic agent system prompts.
  Items carry an admin-only optional `tenant_id` (cache keys are tenant-scoped),
  fan-out is bounded, and per-item failures are reported instead of raised.
- [x] **Cache analytics visualizations** *(2026-11-04)*: Recharts in `web/`.
  The `Cache Performance` composition donut (Redis exact / Qdrant exact /
  semantic / miss), `Top Intents` bars, cache hit rate over time, the
  latency-breakdown waterfall, routing-decision distribution as its own chart,
  and cumulative cost avoided. Hit rate over time needed one additive backend
  field: `time_series[]` now carries `cache_hits` and `llm_calls` per bucket, so
  the chart cannot disagree with the window-level donut. Cost avoided is
  explicitly labelled an estimate — each hit is priced at the window's mean cost
  per LLM call, because the counterfactual cost of a hit is not recorded.
- [x] **Cache entry inspector / manual eviction UI** *(2026-11-04)*: a
  "Cache Explorer" page in `web/` that searches entries by prompt text
  (`POST /api/v1/admin/cache/search`, semantic, with tenant/model/scope/intent
  filters) or by exact hash (`mode: "exact"`, no inference call), shows the
  payload metadata (scope, intent, tenant, TTL, hit count, score, entities,
  service, core operation/subject, prompt/reranker/key versions, cost, latency,
  tokens, retention value) plus the latest audit verdict for the point
  (rerank score, vector similarity, equivalence, audit time), and evicts one
  entry across Qdrant + Redis (`DELETE /api/v1/admin/cache/entry/{point_id}`)
  or a filtered slice (`POST /api/v1/admin/cache/evict` by intent, model, scope,
  tenant, age, or expired-only, dry-run by default, capped at 1000 per call).
  All three are admin-scoped thin wrappers over
  `llm_cache_collection_service`.
- [x] **Diagnostics hardening** *(2026-11-04)*: `/api/v1/qdrant/search` and
  `/search/rrf` require `cache:read` and run their blocking Qdrant/BM25 calls in
  `asyncio.to_thread`; `QdrantSearchRequest.threshold` is finally applied
  (dense result set, and the dense prefetch branches for RRF, whose fused score
  is a rank score). `mcp-bridge/` sends `X-Admin-Key` for its cache diagnostic.

---

### Phase 3 — Performance and cost of serving

*Complete (2026-10-29). The execution tracker that drove this phase was deleted
once every step landed; what follows is the record.*

> **Status (2026-10-28):** model serving is fully split. A separate
> `inference-platform/` dependency root serves dense embeddings, reranking,
> relevance, and NLI over HTTP; the gateway consumes it via `INFERENCE_BASE_URL`
> with role routing and starts only against a verified engine. The in-process
> model path was then deleted rather than kept as a fallback, so `torch`,
> `transformers`, and `sentence-transformers` are no longer dependencies at all
> and the image is **0.71GB** (measured; the pre-removal build with in-process
> models was 9.4GB). The original `<300MB` estimate was wrong: `onnxruntime` +
> BM25 put the floor near 0.7GB.
>
> **Status (2026-10-29):** the platform is packaged as a standalone service —
> `pyproject.toml` + `uv.lock` + `src/inference_platform/` with a console entry
> point, no shared environment with the gateway — and its contract gained a
> `sparse` (BM25) role answered by the same `/embeddings` call as the dense
> vectors. Echo Gate itself still computes BM25 locally (its Qdrant collection
> holds vectors from the gateway's own tokenizer, so moving the tokenizer means
> re-indexing); doc-nexus and agent-forge are the consumers for the served role.
> It also gained zero-shot classification (`/classify` with `mode: "zero-shot"`,
> labels as NLI hypotheses) and a real deployment path: CPU-only image, compose
> file, and a Helm chart. Measured limitation: with *noun* labels the shipped NLI
> checkpoint ranks topics well and intents poorly (a phishing mail is "account
> notice" at 0.983), so zero-shot via `hypothesis_template` is a calibrated
> ranking, not a drop-in intent classifier. Sending `hypotheses` instead —
> behaviour-shaped claims with mutually exclusive labels — moves the same model to
> 6/6 with ≥0.93 margins on the intent set; see the contract's "Designing
> hypotheses".

- [x] **High-performance model serving**: embeddings, reranking, relevance, and
  NLI are served by `inference-platform/` over HTTP; `CrossEncoderService` and
  `EmbeddingService` are pure HTTP clients, and `sentence-transformers` /
  `transformers` / `torch` were removed from the dependency set entirely (the
  `local-models` extra is gone). `InferenceClient.verify()` makes a missing,
  unreachable, skewed, or partially-loaded engine a startup failure; runtime
  outages stay fail-open and report as `inference: failed` on
  `/api/v1/health/deps`. Image: 0.71GB (9.4GB before removal — CUDA libraries
  dominated). Multi-stage build keeps poetry and its wheel cache out of the
  runtime image. Implementations: the `inference_platform` service in this repo
  (`uv run inference-platform`) for laptops, Infinity for GPU hosts.
- [x] **Cost-aware cache retention** (`2026-10-29`): cache
  entries record `provider_cost` (from `app/core/pricing.py`), `latency_ms`,
  `token_count`, `hit_count`, and a `retention_value` score. The intent TTL is
  stretched by `(1 + log10(1 + cost * 100)) * (1 + latency_ms / 1000)` up to
  `CACHE_RETENTION_MAX_TTL_SECONDS`, hits bump `hit_count` out of band, and the
  eviction sweep deletes expired entries below
  `CACHE_RETENTION_PROTECT_THRESHOLD` first, keeps valuable ones for a grace
  period, and evicts capacity overflow lowest-value first. Curves live in
  `app/modules/qdrant/retention_policy.py`; knobs in `docs/CONFIGURATION.md`;
  tests in `api/app/modules/qdrant/tests/test_retention_policy.py`.
- [x] **Response cost estimate endpoint** (`2026-10-29`): `POST /api/v1/chat/estimate`
  returns the prompt token count (`tiktoken`, with a length-estimate fallback
  when the encoding file is unavailable) and a cheapest-first cost matrix
  (`cost_prompt`, `cost_completion`, `cost_total`) across every enabled model,
  without calling any provider. `max_tokens` sets the assumed completion
  length; `ESTIMATE_DEFAULT_COMPLETION_TOKENS` (256) covers the omitted case.
  Implementation: `app/modules/chat/chat_estimate_service.py`; tests:
  `api/app/modules/chat/tests/test_chat_estimate.py`.
- [x] **Streaming cache hit optimization** (`2026-10-29`): `X-Cache-Prefer: instant`
  on a `stream: true` request returns a cache hit as one
  `ChatCompletionResponse` JSON body (`X-Cache: HIT`) instead of synthesized
  SSE chunks. The lookup lives in `LlmRouterService.get_instant_cache_hit` and
  reuses the serving-path order, hit counting, audit sampling, and
  usage/trace logging; a miss streams from the provider with
  `skip_cache_lookup=True` so the failed lookups are not repeated. Callers who
  want progressive rendering keep the SSE replay behavior by omitting the
  header.
- [x] **Embedding model upgrade path** (`2026-10-29`): the workflow is documented
  in `docs/DEVELOPMENT.md` ("Embedding model upgrades") as serve → dry run →
  optional high-value seed → full migration → collection switchover → drop old.
  `app/scripts/migrate_embeddings.py` gained `--dry-run`, `--limit`, and
  `--min-retention-value` (a `retention_value` filter, so the expensive entries
  seed first), and the collection name is config-only
  (`QDRANT_COLLECTION_NAME`), so no code edit is needed to swap. Selection and
  limit behavior is unit-tested against an in-memory client; a live run still
  needs Qdrant plus the inference engine.

---

### Phase 4 — Open-core structure and commercialization

Strategy: keep the repo genuinely self-hostable while reserving cloud
operations, identity, and large-org features for the paid tiers. (MCP is
deferred to an external, contract-driven MCP server project.)

**What already works in our favor:**
- RLS multi-tenancy is real — the hard part of shared cloud tenancy is done.
- Metering exists — `llm_usage` cost/token fields plus budget enforcement
  make billing a sync-and-enforce pipeline, not a rewrite.
- Ops surface and packaging exist — health endpoints, structured logging,
  compose services for api/worker/beat, `.dockerignore`, env examples.
- Extension-friendly admin routing — routers are included conditionally
  (`conversation/` proves the pattern), so cloud-only routes can register
  only when the ee package is present.

**Proprietary-code architecture (recommended: overlay distribution):**
The OSS repo stays clean; a private `echo-gate-cloud` repo ships a pip
package (or code overlay) installed only into the cloud image
(`base OSS image + pip echo-gate-cloud`). The core exposes small stable
seams (below). Alternatives for the record: GitLab-style `ee/` directory
under a proprietary license gated by a runtime signed license file (one
tree, but public proprietary code and license policing), or a separate
cloud monorepo reusing core as a library (cleanest IP, worst sync cost).

#### Extension seams to add to the core:

- [x] `app/core/features.py` — feature-flag registry
  (`DEPLOYMENT_MODE=oss|cloud`); no runtime license logic in the OSS build.
  Landed with `register_cloud()` / `is_enabled()` / `mode()` (2026-11-05).
- [x] **Identity protocol**: `app/core/identity.py` — `IdentityProvider`
  protocol; OSS keeps `ApiKeyIdentityProvider`, cloud registers
  `OidcIdentityProvider` plus a session portal. `TenantContextMiddleware`
  is the injection point (2026-11-05).
- [x] **Metering export**: `app/core/metering.py` — `MeteringSink` protocol;
  OSS defaults to `NoopMeteringSink`, the cloud installs a Stripe recorder
  (2026-11-05).
- [x] **Storage/broker hardening**: `app/core/storage.py` — `StorageFactory`
  abstracts the `redis_service`/`qdrant` constructors for managed-service
  connection parameters (TLS, IAM auth, retry policy) (2026-11-05).
- [x] **Admin surface**: `/api/v1/admin/*` kept; `billing:*` routes
  (`api/app/modules/billing/billing_controller.py`) mount only when
  `features.is_enabled("billing")` (2026-11-05).
- [x] **Event bus / webhook sink** *(2026-11-05)*: typed events from background
  tasks (`cache.miss`, `quota.exceeded`, `provider.failure`, `audit.failed`) to
  a configurable sink. OSS defaults to per-type webhook URLs
  (`EVENTS_WEBHOOK_*`); a type with no URL is dropped before any dispatch work,
  and `EVENTS_ENABLED` is a kill switch, so the default build is unchanged.
  Delivery prefers Celery and otherwise runs on a task/daemon thread, so webhook
  I/O is never inline on a request path; failures are logged and ignored
  (fail-open). Payloads exclude prompt/response text and strip credential-shaped
  keys recursively. Cloud replaces the sink with a Stripe/PagerDuty/Slack
  fan-out via `events.set_event_sink()`. Wiring: `_log_usage` (`cache.miss`),
  `check_quota` refusals (`quota.exceeded`), `_call_provider_with_retry`
  (`provider.failure`), `_background_cache_audit` (`audit.failed`). Tests:
  `api/app/core/tests/test_events.py`,
  `api/app/modules/llm/tests/test_event_emission_wiring.py`, plus audit wiring
  cases in `cache_audit/tests/test_cache_audit.py`. Documented in
  `docs/EXTENSION_SEAMS.md` and `docs/CONFIGURATION.md`.

#### Cloud control plane (private repo / ee package):

- [ ] **Portal** (Next.js, or `web/` behind auth): signup, org creation →
  provisions tenant (an `api_keys` row + RLS tenant id), key reveal, plan
  selection.
- [ ] **Metering worker**: reads `llm_usage` rows (cost fields already exist) →
  aggregates → Stripe usage records / internal balance decrement against
  the Redis `budget:` counter.
- [ ] **Billing webhooks**: `trial → active → past_due → deprovisioned` toggles the
  tenant's key status (the existing 402 "budget exhausted" path is the
  enforcement hook).
- [ ] **Provisioning**: shared cluster — tenant = RLS tenant on a shared DB;
  dedicated tier = own Postgres schema / Qdrant collection / Redis
  namespace. Isolation is config, not code.

**Tier table (bright line):**

| Capability | Tier | Rationale |
| --- | --- | --- |
| Gateway, routing, cache, RLS tenancy, quota, admin quota CRUD, traces/analytics | OSS / self-host | The product people will standardize on. |
| Multi-tenant deployment tooling (Helm for operators running it for *their* org) | OSS | Self-hosters are the distribution engine. |
| Managed cloud: signup portal, Stripe metering from `llm_usage`, hosted Postgres/Qdrant/Redis, shared cluster isolation | Paid cloud | Ops load is the value. |
| SSO/SAML/OIDC org login, SCIM, per-seat RBAC UI, audit-log export (SIEM), SOC 2 artifacts | Cloud + Enterprise | The buyers who need them pay for them. |
| Bring-your-own-VPC / dedicated region / on-prem enterprise self-hosted with support and upgrade SLAs | Enterprise | Money is in support & reliability, not gating core features. |
| Cloud billing endpoints (`/api/v1/billing/*`, portal) | Cloud-only | Not shipped in the OSS image; meaningless there. |

Review rule: **every feature OSS users legitimately need to run the product
alone stays OSS.** Reserving operational improvements behind a paywall
raises suspicion and slows adoption.

#### Self-hosting packaging:

- [x] `deploy/compose/` with profiles (`core` vs `ui`), one-liner quickstart
  ending in `python -m app.scripts.create_admin_key create` *(2026-11-05:
  `deploy/compose/docker-compose.yml` + `setup.sh`)*.
- [x] Helm chart for enterprise self-hosted (cert-manager-friendly; values for
  `PYTHON_ENV=production`, pinned image tags, run-migrations-on-deploy job)
  *(2026-11-05: `deploy/helm/echo-gate/` umbrella + `charts/echo-gate-api/`
  sub-chart)*.
- [x] Versioned releases: Git tags, GHCR image digests, `CHANGELOG.md`, semver
  disciplined by the existing OpenAPI snapshot contract test *(2026-11-05:
  `v0.1.0` sources in sync, `scripts/check_versions.py` gate, tag-triggered
  GHCR build)*.
- [x] Opt-in telemetry (`TELEMETRY_ENABLED`, default off; version + instance
  id ping), documented in `docs/` *(2026-11-05:
  `api/app/core/anonymous_telemetry.py`, `TELEMETRY_*` in
  `docs/CONFIGURATION.md`)*.
- [x] **Multi-region cache federation** (architecture-only, *2026-11-05*): design
  recorded in
  [`docs/MULTI_REGION_CACHE_FEDERATION.md`](MULTI_REGION_CACHE_FEDERATION.md)
  — local-first read → provider, async replication of high-value entries
  (`retention_value`-gated, `GLOBAL`/`TENANT` scope only) across regions via
  Celery (`replicate_cache_entry` + daily high-water sweep) over an
  admin-scoped ingestion endpoint. Qdrant payload gains additive
  `region_origin`/`federation_version` with `CACHE_FEDERATION_*` knobs
  (default-off) so the schema never adds region-incompatible assumptions.
  Implemented as design + seams only; replication stays disabled until
  a follow-up lands off these seams.

**Sequencing within this phase**: extension seams (no behavior change,
~4 focused PRs) → OSS launch bound to Phase 0 → cloud distribution
(metering first, then portal, then SSO) → enterprise self-hosted (Helm +
support playbook) last.

---

### Phase 5 — Gateway Evolution: Protocol Expansion, Optimization & Governance

Expanding Echo Gate's gateway surface, prompt efficiency, and safety guardrails, drawing on battle-tested design patterns from modern open-source LLM gateways.

- [x] **1. Prompt Token Optimization Pipeline** *(2026-11-06)*
  - **Motivation**: Shaving input tokens before upstream LLM dispatch directly lowers inference costs, reduces TTFT (time-to-first-token), and prevents cache corpus bloat.
  - [x] **Lossless & Rule-Based Compaction** *(2026-11-06)*:
    - Whitespace & boilerplate compaction: collapse repetitive spaces, tabs, and duplicate newline runs across prompt turns (preserving markdown code blocks and inline code).
    - Tool-result tabularization (`json-table`): rewrite large uniform JSON arrays returned from tool calls into compact markdown/pipe-delimited tables, dropping redundant key names (with size-reduction invariant).
    - Turn deduplication (`session-dedup`): detect exact-match duplicate message turns in lengthy multi-turn sessions (preserving atomic tool turns and active user prompt).
  - [x] **Context Window & Headroom Management** *(2026-11-06)*:
    - Conversation context reduction (`ccr`): retain system prompt and last $N$ turns while compressing or omitting intermediate turns with notice.
    - Headroom trimmer: dynamically prune oldest intermediate turns to guarantee prompt fit within the selected model's context window minus completion tokens.
    - Configured via request body `prompt_optimization` or header `X-EchoGate-Prompt-Optimization: ccr,headroom-trim` and `X-EchoGate-Prompt-Optimization-CCR-Turns`. Tested in unit and integration suites.
  - [x] **Lossy Prompt Compression (Phase C)** *(2026-11-06)*:
    - Offload token pruning (`method="lexical"`) and sentence centroid ranking (`method="extractive"`) to `inference-platform/` over HTTP via `POST /compress` to strictly preserve Echo Gate's architectural invariant of zero in-process ML models.
    - Supported via request body `prompt_optimization.compress` and `compress_rate`, `compress_method`, reporting savings via `X-EchoGate-Prompt-Savings`.

- [x] **2. Anthropic Messages Protocol Adapter (`POST /api/v1/messages`)** *(2026-11-07)*
  - **Motivation**: Enable seamless drop-in integration for Anthropic SDK clients (Claude Code, Cursor, OpenCode, Python/TS `anthropic` SDKs) without client-side translation layers.
  - **Bidirectional Wire Translation**:
    - Normalize Anthropic request format (`system` string/array, `messages` with structured text/image blocks) to Echo Gate's internal representation.
    - Translate gateway responses back into native Anthropic `message` structures and stop reasons.
  - **Streaming Parity**:
    - Map internal streaming tokens to Anthropic SSE event format (`message_start`, `content_block_start`, `content_block_delta`, `message_delta`, `message_stop`).
    - Support instant and natural-paced cache hit playback directly into Anthropic SSE stream format.
  - **Contract Integrity**:
    - Snapshot contract tests updated and verified in `tests/contract/test_openapi_snapshot.py`. Mounted at `/api/v1/messages` and alias `/v1/messages`.

- [ ] **3. Zero-Retention Mode & In-Flight PII Redaction**
  - **Zero-Retention Mode (`disable_log`)**:
    - Allow callers to pass `disable_log: true` or `X-EchoGate-Disable-Log: true` to bypass recording into PostgreSQL (`gateway_requests_log`, `llm_usage_log`) and cache stores (Redis / Qdrant) for HIPAA/GDPR zero-trace compliance.
  - **Pre-Routing PII Scrubber**:
    - Detect and mask sensitive entity types (Email, Phone, Credit Card with Luhn validation, SSN, IBAN) *prior* to cache key generation or provider transmission.
    - Guarantees sensitive customer data is never indexed into Qdrant vector store, Redis cache keys, or external provider logs.
  - **Soft-Steering Guardrails**:
    - Hard-blocking mode: evaluate inputs against policy rules and abort request with structured audit logs.
    - Soft-steering mode (`inject`): append policy instructions directly to the system prompt, directing the downstream LLM to self-enforce behavior without failing the call.

- [x] **4. Per-Request Gateway Directives (`cache_enabled`, `cache_ttl`, `retries`)** *(2026-11-07)*
  - **Directives & Overrides**:
    - Allow client callers to override gateway caching and retry behavior per request via body (`cache_enabled`, `cache_ttl`, `retries`) and headers (`X-EchoGate-Cache`, `X-EchoGate-Cache-TTL`, `X-EchoGate-Retries`).
    - Support `cache_enabled: false` (cache bypass / force refresh), custom `cache_ttl` in seconds (overriding retention policy calculation in Redis and Qdrant), and per-request `retries` bounds.

- [x] **5. Natural Streaming Playback for Cache Hits** *(2026-11-07)*
  - **Smooth Token/Word Chunking**:
    - Replace monolithic single-frame SSE responses for streaming cache hits with natural word/token chunking and micro-interval pacing (`STREAM_CACHE_PACING_DELAY_MS`) to eliminate UI flickering and support typewriter rendering on streaming frontends.
    - Support `X-EchoGate-Stream-Pacing: off` client opt-out while preserving `ChatCompletionChunk` SSE contract.

---

### Cross-cutting (independent of phase)

Work that benefits every phase and can be tackled alongside any milestone.

- [x] **Prompt-level cost attribution** *(2026-11-04)*: `llm_usage_logs.prompt_hash`
  (SHA-256 of the *system* prompt only, whitespace-normalized) is written by
  `LlmRouterService._log_usage` via `app.core.prompt_identity`. That module owns
  both directions of the prompt encoding, so write-time hashing and the backfill
  cannot drift. History is filled by
  `python -m app.scripts.backfill_prompt_hashes [--batch-size N] [--dry-run]`,
  which recovers the system prompt from stored `input_text` and **skips**
  unrecoverable rows (no system message, or content containing the segment
  separator) rather than guessing. The dashboard exposes
  `GET /api/v1/analytics/dashboard` → `prompt_breakdown` (requests, cost, tokens
  per hash, most expensive first); NULL hashes form an unattributed bucket.
  Migration `f6a7b8c9d0e1`. Tests: `analytics/tests/test_prompt_attribution.py`.
- [x] **OpenTelemetry (OTel) trace export** *(2026-11-04)*: optional OTel
  instrumentation emitting spans for each request lifecycle stage (cache lookup,
  embedding generation, provider call, cache write). `X-Correlation-ID` is the
  trace id. Export is opt-in: `OTEL_ENABLED` default `true`, but with
  `OTEL_EXPORTER_OTLP_ENDPOINT` unset traces stay in-process as a zero-overhead
  NoOp. `init_telemetry()` runs in the app lifespan and is fail-open — a bad
  endpoint logs a warning and leaves tracing NoOp, never blocking startup.
  Dependencies: `opentelemetry-api`, `opentelemetry-sdk`,
  `opentelemetry-exporter-otlp`.
- [x] **Test coverage expansion** *(2026-11-04)*: every listed path now has a
  dedicated file. Cache write failure isolation:
  `llm/tests/test_cache_write_failure_isolation.py`; fallback provider
  switchover: `llm/tests/test_router_resilience.py` (blocking and streaming);
  intent classifier timeout/degradation:
  `intent_classifier/tests/test_intent_classifier_degradation.py`; conversation
  chat with a cache hit:
  `conversation/tests/test_conversation_cache_hit.py`; concurrent quota
  enforcement: `llm_quota/tests/test_llm_quota_concurrency.py`; analytics
  aggregation: `analytics/tests/test_analytics_aggregation.py`.

## Model selection: laptop defaults vs cloud release

The defaults in `inference-platform/models.lock.yaml` are chosen for a 16 GB
laptop: each is the smallest checkpoint that is genuinely good at its job. The
measured cost of the five dense/cross-encoder roles is **1419 MB (was 1809 MB)**,
plus ~5 MB for BM25, with better quality in every role, and latency on an
M4/MPS stays at 10-30 ms per request.

| role | local default (dim/disk) | why it stays | production option |
| :--- | :--- | :--- | :--- |
| `embed` | `BAAI/bge-small-en-v1.5` (384, 128 MB) | biggest quality step that keeps `dim=384`, so the Qdrant schema does not change: paraphrase 0.85 vs 0.62 for MiniLM | `Alibaba-NLP/gte-modernbert-base` (768) for long context, `Qwen/Qwen3-Embedding-0.6B` (1024) for multilingual / best quality |
| `rerank` | `Alibaba-NLP/gte-reranker-modernbert-base` (598 MB) | half the size of `bge-reranker-base` (1.08 GB) and the only candidate that separates short query↔prompt pairs on the *raw-logit* scale the confidence gate uses (0.80 relevant / 0.17 unrelated, vs 0.24 / 0.00) | `Qwen/Qwen3-Reranker-0.6B` (best multilingual, causal-LM head) or drop-in `jinaai/jina-reranker-v2-base-multilingual`, `BAAI/bge-reranker-v2-m3` |
| `relevance` | `cross-encoder/ms-marco-MiniLM-L-6-v2` (91 MB) | already at the small-but-capable point for the 1..5 relevance scale | `gte-reranker-modernbert-base`, or `ms-marco-MiniLM-L12-v2` for a cheap step up |
| `nli` | `cross-encoder/nli-MiniLM2-L6-H768` (316 MB) | same verdicts as `nli-deberta-v3-small` at 552 MB, 2x faster | `tasksource/ModernBERT-base-nli` — its label order differs, so the gateway's positional `[contradiction, entailment, neutral]` indexing must become label-keyed first |
| `zero-shot` | `MoritzLaurer/ModernBERT-base-zeroshot-v2.0` (289 MB) | binary entailment: 0.974 invoice→billing, 0.998 password reset (the 3-way `nli` model scored 0.956 / 0.079); intent labels need claim-shaped `hypotheses`, not noun labels (6/6 vs 5/6, measured) | `MoritzLaurer/ModernBERT-large-zeroshot-v2.0` (792 MB) |
| `sparse` | `Qdrant/bm25` (~5 MB) | tokenizer identity is the contract; no reason to move | same |

### Before a cloud release

1. **Pick the upgrades you actually need.** Dense and rerank are the two that buy
   measurable quality; a single English deployment rarely needs the multilingual
   variants.
2. **`embed` first, because it changes storage.** A `dim` change needs a new Qdrant
   collection (`dim` is fixed at creation), then re-embed:
   `QDRANT_COLLECTION... : poetry run python -m app.scripts.migrate_embeddings --target embeddings_multivector_v2 --write`.
   Without the re-embed, old points are not *corrupted*, they are quarantined: the
   semantic search filters on `payload.embedding_model`, so a warm cache simply
   goes cold.
3. **Bump the version tags that make a swap visible**: `EMBEDDING_VERSION`,
   `RERANKER_VERSION`, and `PROMPT_VERSION` if prompts changed. Cached points are
   filtered on all of them.
4. **Recalibrate the scores the swap moves — this is not optional for `rerank`.**
   `InferenceClient.rerank` returns `score_raw`, and `CACHE_CONFIDENCE_THRESHOLDS`
   (0.85–0.98) are compared against that raw logit scale. A model with the same
   ranking but a different scale silently turns the semantic cache off: the
   146 MB mxbai reranker scored 0.06 raw for a paraphrase where this one scores
   0.80, so nothing ever cleared the gate. Run
   `python -m app.scripts.calibrate_reranker --source audit` once shadow audits
   have accumulated (or `--source static` for an offline replay), then check the
   eval goldens (`inference-platform/evals/check.py`) — several of them pin a
   specific model's spread on purpose. Verify with a live shadow request that a
   known paraphrase lands in the band you expect, not merely that the ranking is
   right.
5. **Pin revisions.** `uv run python tools/pin_models.py --write` in
   `inference-platform/` records the Hugging Face SHAs so a cloud rollout cannot
   silently pull a different checkpoint than staging.
6. **Warm the weights.** Bake them into the image (`--build-arg PREFETCH=1`) or
   give the chart a model PVC with `modelCache.prefetch.enabled=true`; a cold
   engine pays a 2.5 GB download before it can serve.
