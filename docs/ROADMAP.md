# Echo Gate Roadmap

This document is the single history, forward plan, and active work queue for
Echo Gate. It absorbs the former root-level `plans.md`,
`evaluation_pipeline_plans.md`, and `PRIORITIES.md` so they no longer need to be
tracked separately. It has three parts:

- **Active queue** — what is open right now and the rules for working on it.
- **Delivered history** — the shipped feature record and completed phases, kept in
  [`archive/ROADMAP_HISTORY.md`](archive/ROADMAP_HISTORY.md).
- **Planned** — approved architectural work still open, in suggested development
  order.

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

Adopted capabilities that already exist are recorded in the
[delivery archive](archive/ROADMAP_HISTORY.md#landed); everything below is open
or future work.

---

## Active queue

<<<<<<< HEAD
=======

One row per phase, in working order. The detail for every item lives in its phase
section below; this table exists so a reader knows where to start without reading
the whole document.

| Phase                               | State                                                                                   | Start here                                                                         |
| ----------------------------------- | --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| 0 — OSS launch hygiene              | ✅ complete                                                                             | —                                                                                  |
| 1 — Provider resilience             | ✅ complete                                                                             | —                                                                                  |
| 1.5 — Shadow cache pipeline         | ✅ complete                                                                             | —                                                                                  |
| 2 — Cache-quality loop              | ✅ complete                                                                             | —                                                                                  |
| 3 — Serving performance             | ✅ complete                                                                             | —                                                                                  |
| 4 — Open-core & commercialization   | Seams landed; the remaining portal/metering items are cloud-tier work outside this repo | —                                                                                  |
| 5 — Gateway evolution               | ⏳ **one item open**: zero-retention mode & in-flight PII redaction                     | [Phase 5](#phase-5--gateway-evolution-protocol-expansion-optimization--governance) |
| 6 — Interop & SDK parity            | ✅ complete (2026-11-18)                                                                | —                                                                                  |
| 7 — SaaS operations & compliance    | ⏳ planned                                                                              | [Phase 7](#phase-7--saas-operations--compliance)                                   |
| 8 — Data plane & commercial surface | ⏳ planned; BYOK designed, not started                                                  | [Phase 8](#phase-8--data-plane--commercial-surface)                                |
| 9 — Cache backend portability       | ⏸ tracked, not scheduled                                                                | [Phase 9](#phase-9--cache-backend-portability-pgvector-dense-first)                |

**Rules:**

1. Work top to bottom. Implement one item, or one clearly bounded slice, at a time.
2. Preserve the non-negotiable invariants: fail-open caching, non-owner Postgres
   role for RLS, and no in-process embeddings (`AGENTS.md`).
3. Every route or schema change updates and verifies
   `tests/contract/test_openapi_snapshot.py`.

**Baseline quality gate:**
`poetry run python -m compileall api/app &&
poetry run pytest tests/contract/test_openapi_snapshot.py && poetry run pytest tests/`.

**Explicitly out of scope:** OpenAI Responses API, Batch API, audio/image
generation, an in-repo MCP endpoint, SSO/SCIM, the hosted portal, and Stripe
metering. Zero-retention/PII redaction stays open under Phase 5, not duplicated
here.

---

## Landed

> > > > > > > d3eed55760e3f292c1bc1bbfd3c7ad7805cb9017

One row per phase, in working order. The detail for every item lives in its phase
section below; this table exists so a reader knows where to start without reading
the whole document.

| Phase                               | State                                                                                   | Start here                                                                         |
| ----------------------------------- | --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| 0 — OSS launch hygiene              | ✅ complete                                                                             | —                                                                                  |
| 1 — Provider resilience             | ✅ complete                                                                             | —                                                                                  |
| 1.5 — Shadow cache pipeline         | ✅ complete                                                                             | —                                                                                  |
| 2 — Cache-quality loop              | ✅ complete                                                                             | —                                                                                  |
| 3 — Serving performance             | ✅ complete                                                                             | —                                                                                  |
| 4 — Open-core & commercialization   | Seams landed; the remaining portal/metering items are cloud-tier work outside this repo | —                                                                                  |
| 5 — Gateway evolution               | ⏳ **one item open**: zero-retention mode & in-flight PII redaction                     | [Phase 5](#phase-5--gateway-evolution-protocol-expansion-optimization--governance) |
| 6 — Interop & SDK parity            | ✅ complete (2026-11-18)                                                                | —                                                                                  |
| 7 — SaaS operations & compliance    | ⏳ planned                                                                              | [Phase 7](#phase-7--saas-operations--compliance)                                   |
| 8 — Data plane & commercial surface | ⏳ planned; BYOK designed, not started                                                  | [Phase 8](#phase-8--data-plane--commercial-surface)                                |
| 9 — Cache backend portability       | ⏸ tracked, not scheduled                                                                | [Phase 9](#phase-9--cache-backend-portability-pgvector-dense-first)                |

**Rules:**

1. Work top to bottom. Implement one item, or one clearly bounded slice, at a time.
2. Preserve the non-negotiable invariants: fail-open caching, non-owner Postgres
   role for RLS, and no in-process embeddings (`AGENTS.md`).
3. Every route or schema change updates and verifies
   `tests/contract/test_openapi_snapshot.py`.

**Baseline quality gate:**
`poetry run python -m compileall api/app &&
poetry run pytest tests/contract/test_openapi_snapshot.py && poetry run pytest tests/`.

<<<<<<< HEAD
**Explicitly out of scope:** OpenAI Responses API, Batch API, audio/image
generation, an in-repo MCP endpoint, SSO/SCIM, the hosted portal, and Stripe
metering. Zero-retention/PII redaction stays open under Phase 5, not duplicated
here.
=======

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
      Re-scoped _(2026-11-04)_: the reads need an admin key with
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
- [x] **CI quality, security & contract gates** _(2026-11-06)_: CI triggers on
      `[master, staging]`; the docs capability-drift guard
      (`scripts/check_doc_capabilities.py`) runs in the backend gate; coverage floor
      `--cov-fail-under=70` with an HTML artifact; `gitleaks` secret scanning;
      dependency review plus `pip-audit` / `npm audit`; a weekly security cron
      (`.github/workflows/security.yml`); and Dependabot automation with auto-merge
      disabled pending review.

> > > > > > > d3eed55760e3f292c1bc1bbfd3c7ad7805cb9017

---

## Delivered history

The feature-by-feature record of shipped behavior and the completed phases
(0, 1, 1.5, 2, 3, 4) now live in
[`archive/ROADMAP_HISTORY.md`](archive/ROADMAP_HISTORY.md). Their headings are
unchanged, so `ROADMAP.md#...` links to landed sections still resolve there.
This page carries only what is open or planned.

## Planned

Suggested development order. Earlier phases keep the core product safe and
reviewable; later phases add the commercial surface. Every phase is
independently shippable and checkable.

Phases 0 through 4 are complete and archived; the plan below starts at the
first phase with open or planned work.

### Phase 5 — Gateway Evolution: Protocol Expansion, Optimization & Governance

Expanding Echo Gate's gateway surface, prompt efficiency, and safety guardrails, drawing on battle-tested design patterns from modern open-source LLM gateways.

- [x] **1. Prompt Token Optimization Pipeline** _(2026-11-06)_
  - **Motivation**: Shaving input tokens before upstream LLM dispatch directly lowers inference costs, reduces TTFT (time-to-first-token), and prevents cache corpus bloat.
  - [x] **Lossless & Rule-Based Compaction** _(2026-11-06)_:
    - Whitespace & boilerplate compaction: collapse repetitive spaces, tabs, and duplicate newline runs across prompt turns (preserving markdown code blocks and inline code).
    - Tool-result tabularization (`json-table`): rewrite large uniform JSON arrays returned from tool calls into compact markdown/pipe-delimited tables, dropping redundant key names (with size-reduction invariant).
    - Turn deduplication (`session-dedup`): detect exact-match duplicate message turns in lengthy multi-turn sessions (preserving atomic tool turns and active user prompt).
  - [x] **Context Window & Headroom Management** _(2026-11-06)_:
    - Conversation context reduction (`ccr`): retain system prompt and last $N$ turns while compressing or omitting intermediate turns with notice.
    - Headroom trimmer: dynamically prune oldest intermediate turns to guarantee prompt fit within the selected model's context window minus completion tokens.
    - Configured via request body `prompt_optimization` or header `X-EchoGate-Prompt-Optimization: ccr,headroom-trim` and `X-EchoGate-Prompt-Optimization-CCR-Turns`. Tested in unit and integration suites.
  - [x] **Lossy Prompt Compression (Phase C)** _(2026-11-06)_:
    - Offload token pruning (`method="lexical"`) and sentence centroid ranking (`method="extractive"`) to `inference-platform/` over HTTP via `POST /compress` to strictly preserve Echo Gate's architectural invariant of zero in-process ML models.
    - Supported via request body `prompt_optimization.compress` and `compress_rate`, `compress_method`, reporting savings via `X-EchoGate-Prompt-Savings`.

- [x] **2. Anthropic Messages Protocol Adapter (`POST /api/v1/messages`)** _(2026-11-07)_
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
    - Detect and mask sensitive entity types (Email, Phone, Credit Card with Luhn validation, SSN, IBAN) _prior_ to cache key generation or provider transmission.
    - Guarantees sensitive customer data is never indexed into Qdrant vector store, Redis cache keys, or external provider logs.
  - **Soft-Steering Guardrails**:
    - Hard-blocking mode: evaluate inputs against policy rules and abort request with structured audit logs.
    - Soft-steering mode (`inject`): append policy instructions directly to the system prompt, directing the downstream LLM to self-enforce behavior without failing the call.

- [x] **4. Per-Request Gateway Directives (`cache_enabled`, `cache_ttl`, `retries`)** _(2026-11-07)_
  - **Directives & Overrides**:
    - Allow client callers to override gateway caching and retry behavior per request via body (`cache_enabled`, `cache_ttl`, `retries`) and headers (`X-EchoGate-Cache`, `X-EchoGate-Cache-TTL`, `X-EchoGate-Retries`).
    - Support `cache_enabled: false` (cache bypass / force refresh), custom `cache_ttl` in seconds (overriding retention policy calculation in Redis and Qdrant), and per-request `retries` bounds.

- [x] **5. Natural Streaming Playback for Cache Hits** _(2026-11-07)_
  - **Smooth Token/Word Chunking**:
    - Replace monolithic single-frame SSE responses for streaming cache hits with natural word/token chunking and micro-interval pacing (`STREAM_CACHE_PACING_DELAY_MS`) to eliminate UI flickering and support typewriter rendering on streaming frontends.
    - Support `X-EchoGate-Stream-Pacing: off` client opt-out while preserving `ChatCompletionChunk` SSE contract.

---

### Phase 6 — Interop & SDK drop-in parity

**Status: ✅ complete (2026-11-18).** No changes to the cache, resilience, or
tenancy invariants. Goal: an application written against the official OpenAI or
Anthropic SDK can switch `base_url` to Echo Gate and keep working without a
translation proxy, a dummy API key, or client-side header overrides. Gate held:
snapshot test, SDK smoke (`openai` + `anthropic` clients), and the existing suite.

Order within the phase is deliberate: these are small, independently shippable
diffs, and the first three unblock SDK smoke tests that validate the rest.

- [x] **1. Bearer-token auth parity** _(2026-11-17)_ — accept
      `Authorization: Bearer <key>` as an alias for `X-API-Key`, resolved through
      the same server-side key hash lookup. Never trust any caller-supplied
      tenant/user value from the header. Extra beyond the original scope:
      `Authorization: Bearer` also feeds the quota bucket and `request.state.api_key`
      (so a Bearer caller is not billed to the anonymous fingerprint), and the
      OpenAPI document advertises `BearerAuth` as an alternative on strict routes.
      Where: `api/app/core/middlewares.py`, `api/app/core/identity.py`,
      `api/app/core/constants.py`. Acceptance: the OpenAI Python/TS SDK works with
      only `base_url` + `api_key`; `X-API-Key` and Anthropic's `x-api-key` keep
      working; tests cover header precedence, malformed values, and unknown keys.
- [x] **2. Model discovery `GET /v1/models` (and `/api/v1/models`)** _(2026-11-17)_ —
      returns an OpenAI-shaped list from `DynamicModelRegistry`, including aliases
      (`fast`, `cheap`, `quality`) and provider availability. Disabled models and
      models on unconfigured providers stay hidden by default; an admin
      `?include_unavailable=true` exposes them with `available`/`enabled` flags.
      The Anthropic Models API envelope (`type`/`display_name`/`created_at`) is
      selected by the `anthropic-version` header the Anthropic SDK always sends, so
      `models.list()` works from both SDKs. Where:
      `api/app/modules/llm/models_{controller,service,schema}.py`,
      `model_registry.get_aliases()`,
      `LlmProviderService.service_availability()`. Acceptance: `client.models.list()`
      succeeds from both SDKs (verified against openai 3.52 and anthropic 2.9);
      snapshot contract updated; response exposes model id and provider.
- [x] **3. Error envelope parity** _(2026-11-17)_ — non-streaming failures now
      answer with the provider-native envelope instead of FastAPI `{"detail": ...}`,
      matching the OpenAI-shaped error the SSE path already emitted: OpenAI
      `{"error": {...}}` for `/api/v1/chat/completions`, Anthropic
      `{"type": "error", "error": {...}}` for `/api/v1/messages` and its `/v1`
      alias. Implemented app-wide in `core/error_envelopes.py`
      (`install_error_envelopes`, exact-path `surface_for_path`); every other route
      keeps the default body. `error_code` remains the OpenAI `error.code` and is
      echoed in `X-EchoGate-Error-Code`; `Retry-After`/`X-RateLimit-*` pass through,
      and error responses carry `X-Gateway-Degradation` (`none` for `4xx`,
      `hard_down` for terminal `5xx`). Validation keeps status `422`. Verified
      against openai 3.52 (`RateLimitError`, `UnprocessableEntityError`) and
      anthropic 2.9 (`ServiceUnavailableError` on `503`, `RateLimitError` on `429`).
      Where: `api/app/core/error_envelopes.py`,
      `chat/chat_controller.py`, `anthropic/anthropic_controller.py`,
      `core/tests/test_error_envelopes.py`; snapshot updated (additive schemas plus
      the two intentional operation changes).
- [x] **4. OpenAI request-field parity + bounded `extra_body`** _(2026-11-18)_ — added
      `stream_options` (`include_usage`), `seed`, `logprobs`/`top_logprobs`,
      `parallel_tool_calls`, `metadata`, and size-limited `extra_body` passthrough
      (≤64 keys / 64 KiB, merged into the upstream payload) to
      `ChatCompletionRequest`. Unsupported fields now raise a 422 instead of being
      silently dropped; the new answer-affecting fields participate in the exact
      cache key. Streamed responses emit a final usage chunk when
      `stream_options.include_usage` is set (upstream passthrough). Where:
      `chat/chat_schema.py`, `llm/providers/common.py`,
      `llm/llm_router_service.py`. Acceptance: snapshot updated; cache-key coverage
      via `_generate_cache_key`.
- [x] **5. Upstream provider prompt-cache passthrough** _(2026-11-18)_ — Anthropic
      `cache_control` blocks are preserved through the adapter (private annotation on
      `ChatMessage`, re-attached by the Claude payload builder, block-list `system`
      when annotated) instead of being flattened away. OpenAI and Gemini implicit
      cache counts are mapped onto `Usage` (`prompt_tokens_details.cached_tokens`);
      Anthropic's `cache_read_input_tokens`/`cache_creation_input_tokens` are kept,
      with `prompt_tokens` normalized to total input. `llm_usage_logs` gains
      `upstream_cached_tokens`, `upstream_cache_read_input_tokens`, and
      `upstream_cache_creation_input_tokens` (separate from the gateway cache
      columns) and the cost job prices them with `core/pricing.py` multipliers
      (OpenAI 0.5x reads; Anthropic 0.1x reads / 1.25x writes; Gemini 0.25x reads).
      This is labelled **upstream prompt cache** everywhere, orthogonal to Echo
      Gate's semantic cache. Where: `anthropic/anthropic_adapter.py`,
      `llm/providers/{claude,gemini}.py`, `core/pricing.py`,
      `LlmRouterService._log_usage`, `llm_usage/{model,schema,tasks}.py`,
      `alembic/versions/a7b8c9d0e1f2_*`.
- [x] **6. Multimodal content preservation** _(2026-11-18)_ — OpenAI `content`
      arrays (text + `image_url`) and Anthropic `image`/`document` blocks are kept as
      ordered private parts on `ChatMessage` and translated per provider instead of
      being flattened or dropped. Unsupported block types fail loudly (422/400).
      Semantic caching stays text-only: non-text requests bypass the cache unless
      `cache_enabled: true` is set, in which case only exact lookups run and their
      keys include a digest of the ordered parts. The decision is visible in
      `X-Cache` on both streaming and non-streaming paths. Where:
      `chat/chat_schema.py`, `anthropic/anthropic_adapter.py`,
      `llm/providers/{common,claude,gemini}.py`, `llm/llm_router_service.py`,
      `chat/chat_controller.py`, `anthropic/anthropic_controller.py`,
      `prompt_optimization/prompt_optimizer.py`. Tests:
      `tests/integration/test_multimodal_preservation.py`.

---

### Phase 7 — SaaS operations & compliance

**Status: ⏳ planned (opened 2026-11-17).** What a shared, multi-tenant
deployment needs before an operator will run customer traffic on it. These are
operational capabilities, not cache features, and they must stay fail-open: no
new 5xx path may be introduced by metrics, audit, retention, or delivery code.

- [ ] **1. Prometheus metrics + shipped dashboards** — add a scrapeable
      `/metrics` endpoint (or OTel metrics exporter) covering request rate, error
      rate, latency histograms, cache hit rate by layer, estimated time/cost saved,
      quota rejections, provider errors, circuit-breaker state, and worker queue
      depth. Ship a Grafana dashboard, a Helm `ServiceMonitor`, and `PrometheusRule`
      alerts (error rate, breaker open, queue lag, budget exhaustion). Acceptance:
      scrape works locally; the chart renders with monitoring enabled; metrics
      initialization failure is logged and never blocks startup.
- [ ] **2. Log/data retention and tenant erasure** — configurable retention for
      `gateway_requests`, `llm_usage`, `shadow_cache_evaluations`, and
      `cache_evaluations`, run by a Celery beat sweep under the system RLS context.
      Add admin-scoped tenant export and erasure endpoints that remove DB rows and
      the tenant's Redis/Qdrant cache entries, with a dry-run mode. Acceptance:
      expired rows are deleted on schedule; erasure removes DB and cache data;
      export is RLS-scoped and never returns another tenant's rows.
- [ ] **3. Admin audit trail** — append-only `admin_audit_log` table (actor admin
      key id, action, target, request id, timestamp) written by every privileged
      route: key mint/rotate/revoke, quota CRUD, cache evict/warmup, retention, and
      erasure. Admin read endpoint with filters. Acceptance: no prompt/response
      text or secret material is stored; every privileged route writes exactly one
      row; migration and tests included.
- [ ] **4. Per-tenant fairness** — add `max_concurrency` to quota rules and a
      per-tenant semaphore alongside the existing global/provider bulkheads.
      Preserve fail-open semantics: a tenant cap sheds with `429`, never blocks
      other tenants or strands a half-open circuit-breaker permit. Acceptance: one
      tenant cannot exhaust `GLOBAL_MAX_CONCURRENCY`; permits are released on
      success, failure, fallback, shed, and stream cancellation.
- [ ] **5. Webhook delivery hardening** — sign deliveries with HMAC-SHA256
      (`X-EchoGate-Signature`, delivery id, timestamp), add bounded exponential
      retries with jitter, and persist exhausted deliveries in a dead-letter
      table/queue. Existing "unset URL = dropped before dispatch" and the
      `EVENTS_ENABLED` kill switch stay unchanged. Acceptance: consumers can verify
      signatures; transient failures retry; exhausted deliveries are inspectable;
      no prompt text or credential keys enter the payload.
- [ ] **6. Idempotency keys** — accept `Idempotency-Key` on completions and
      messages and replay the stored response for the same tenant + key + request
      hash, preventing double billing and duplicate traces on client retries.
      Define and document streaming behavior (bounded buffer replay or an explicit
      rejection). Acceptance: a retried request returns the same body and does not
      double-count usage; the same key with a different body returns `409`; expired
      keys fall through to normal processing.

---

### Phase 8 — Data plane & commercial surface

**Status: ⏳ planned (opened 2026-11-17).** Expands what the gateway sells and
what a SaaS builder can do without standing up a second service. Every item
reuses the existing auth, quota, metering, RLS, and observability layers.

- [ ] **1. OpenAI-compatible `/v1/embeddings` and Cohere/Jina-compatible
      `/v1/rerank`** — serve through `inference-platform/` over HTTP, preserving the
      no-in-process-ML invariant, with standard auth, quota, metering, and trace
      logging. Unlike cache lookups, these are primary capabilities: an inference
      outage returns a structured `503`, not a fail-open fallback. Acceptance:
      OpenAI SDK embeddings and a standard rerank client both work; quota is
      consumed; snapshot contract updated; docs state the failure semantics.
- [ ] **2. Per-tenant BYOK provider credentials** — let a tenant store its own
      provider keys (envelope-encrypted at rest with a master key/KMS, tenant-scoped
      lookup before the env fallback, rotation via admin, never logged or emitted in
      events/traces). Acceptance: tenant A's key is never used for tenant B; the
      platform env credential is used only when the tenant has **no** credential for
      that provider (a present-but-unusable BYOK credential fails closed, never
      silently falls back); rotation is audited; a compromised key can be revoked
      without a redeploy.
      **Designed 2026-11-25, not started** — see
      [`docs/adr/0005-byok-provider-credentials.md`](adr/0005-byok-provider-credentials.md)
      for the threat model, schema, delivery slices, and residual risks. Decisions
      already locked: separate `provider_credentials` resource (never reuse
      `api_keys`); AES-256-GCM with the KEK outside the DB and `AAD` binding
      tenant/provider/row; per-call credential passing (never mutate the provider
      singleton); **fail closed with no silent env fallback**; credential-scoped
      breakers; platform quota skipped but metering kept (`billed=false`); cache key
      unchanged (already tenant-scoped). Security review required before slice 1.
- [ ] **3. Per-tenant model allowlists and plan gating** — resolve an effective
      model set per tenant/key, enforce it in `/chat/completions`, `/messages`, and
      `/v1/models`, and return `403` with the allowed set on violation. Acceptance:
      a plan cannot call a gated model through any surface; aliases resolve within
      the allowed set.
- [ ] **4. Usage attribution metadata** — accept a bounded `metadata` object
      (customer id, feature, environment) on requests, persist it on traces and
      usage, and expose group-by aggregation in analytics. It must not affect cache
      keys or be interpreted as identity. Acceptance: dashboard can group spend by
      tag; metadata size is bounded; no free-form prompt text is accepted.
- [ ] **5. Keys and tenant management UI** — pages built on the existing admin
      API for key create/list/rotate/revoke and per-tenant usage summary. Acceptance:
      plaintext is shown exactly once; scopes are enforced server-side; the UI never
      stores keys in browser storage.
- [ ] **6. Pricing catalog correctness** — move pricing out of the hardcoded
      `PRICING_CONFIG` dict into a config/DB-backed catalog with cached read/write
      rates, and mark unpriced models as `cost_known=false` instead of silently
      pricing them at `$0` (several providers currently default to zero). Acceptance:
      analytics shows an "unpriced" bucket; cached-token costs are correct; a model
      can be re-priced without a code change.

**Explicitly deferred from Phases 6–8:** OpenAI Responses API, Batch API,
audio/image generation, and an in-repo MCP endpoint (the external MCP server
decision stands). SSO/SCIM, the hosted portal, and Stripe metering remain
cloud-tier Phase 4 work. Zero-retention mode, the PII scrubber, and
soft-steering guardrails remain open under P3 and are not duplicated here.

---

### Phase 9 — Cache backend portability (pgvector, dense-first)

**Status: ⏳ tracked (opened 2026-11-20).** Optional cache backend for self-hosters
who do not want to run a separate vector database. Qdrant remains the default and
reference; pgvector is opt-in. See `docs/adr/0004-pgvector-cache-backend.md`.

- [ ] **1. Backend port + seam** — define a narrow cache-backend interface
      (`search_exact`, `query_dense`, `upsert`, `record_hit`, `delete_point`,
      eviction/retention) and move Qdrant-specific `models.Filter`/`Prefetch`/`PointStruct`
      construction behind it; extend `StorageFactory` (`api/app/core/storage.py`) with
      `CACHE_BACKEND=qdrant|pgvector`. Acceptance: router and admin controller depend
      only on the port; Qdrant behavior unchanged (snapshot green).
- [ ] **2. pgvector schema + dense path** — 3 vector columns (prompt/system/user)
      with HNSW, exact-hash unique index, payload/scope indexes, upsert, dense fast
      path (`>= 0.98` cosine gate), dense semantic search feeding the reranker,
      record-hit, and value-aware eviction/retention. Acceptance: exact + dense cache
      hits work end-to-end; fail-open preserved; contract tests pass on both backends.
- [ ] **3. Hybrid parity (deferred)** — sparse retrieval via Postgres FTS or
      ParadeDB, with a versioned sparse tokenizer contract and per-backend calibrated
      thresholds. Not in scope for the first slice; documented as unavailable otherwise.
- [ ] **4. Docs & support** — `docs/limitations.md`, architecture map,
      `docs/capability-evidence.json`, and `/deps`/health naming updated; admin
      endpoints either backend-neutral or backend-gated.

**Explicitly not in scope:** replacing Qdrant as default, claiming behavioral
equivalence, or pgvector multi-region federation.

### Cross-cutting (independent of phase)

Work that benefits every phase and can be tackled alongside any milestone.

- [x] **Prompt-level cost attribution** _(2026-11-04)_: `llm_usage_logs.prompt_hash`
      (SHA-256 of the _system_ prompt only, whitespace-normalized) is written by
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
- [x] **OpenTelemetry (OTel) trace export** _(2026-11-04)_: optional OTel
      instrumentation emitting spans for each request lifecycle stage (cache lookup,
      embedding generation, provider call, cache write). `X-Correlation-ID` is the
      trace id. Export is opt-in: `OTEL_ENABLED` default `true`, but with
      `OTEL_EXPORTER_OTLP_ENDPOINT` unset traces stay in-process as a zero-overhead
      NoOp. `init_telemetry()` runs in the app lifespan and is fail-open — a bad
      endpoint logs a warning and leaves tracing NoOp, never blocking startup.
      Dependencies: `opentelemetry-api`, `opentelemetry-sdk`,
      `opentelemetry-exporter-otlp`.
- [x] **Test coverage expansion** _(2026-11-04)_: every listed path now has a
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

| role        | local default (dim/disk)                              | why it stays                                                                                                                                                                                                     | production option                                                                                                                                                     |
| :---------- | :---------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `embed`     | `BAAI/bge-small-en-v1.5` (384, 128 MB)                | biggest quality step that keeps `dim=384`, so the Qdrant schema does not change: paraphrase 0.85 vs 0.62 for MiniLM                                                                                              | `Alibaba-NLP/gte-modernbert-base` (768) for long context, `Qwen/Qwen3-Embedding-0.6B` (1024) for multilingual / best quality                                          |
| `rerank`    | `Alibaba-NLP/gte-reranker-modernbert-base` (598 MB)   | half the size of `bge-reranker-base` (1.08 GB) and the only candidate that separates short query↔prompt pairs on the _raw-logit_ scale the confidence gate uses (0.80 relevant / 0.17 unrelated, vs 0.24 / 0.00) | `Qwen/Qwen3-Reranker-0.6B` (best multilingual, causal-LM head) or drop-in `jinaai/jina-reranker-v2-base-multilingual`, `BAAI/bge-reranker-v2-m3`                      |
| `relevance` | `cross-encoder/ms-marco-MiniLM-L-6-v2` (91 MB)        | already at the small-but-capable point for the 1..5 relevance scale                                                                                                                                              | `gte-reranker-modernbert-base`, or `ms-marco-MiniLM-L12-v2` for a cheap step up                                                                                       |
| `nli`       | `cross-encoder/nli-MiniLM2-L6-H768` (316 MB)          | same verdicts as `nli-deberta-v3-small` at 552 MB, 2x faster                                                                                                                                                     | `tasksource/ModernBERT-base-nli` — its label order differs, so the gateway's positional `[contradiction, entailment, neutral]` indexing must become label-keyed first |
| `zero-shot` | `MoritzLaurer/ModernBERT-base-zeroshot-v2.0` (289 MB) | binary entailment: 0.974 invoice→billing, 0.998 password reset (the 3-way `nli` model scored 0.956 / 0.079); intent labels need claim-shaped `hypotheses`, not noun labels (6/6 vs 5/6, measured)                | `MoritzLaurer/ModernBERT-large-zeroshot-v2.0` (792 MB)                                                                                                                |
| `sparse`    | `Qdrant/bm25` (~5 MB)                                 | tokenizer identity is the contract; no reason to move                                                                                                                                                            | same                                                                                                                                                                  |

### Before a cloud release

1. **Pick the upgrades you actually need.** Dense and rerank are the two that buy
   measurable quality; a single English deployment rarely needs the multilingual
   variants.
2. **`embed` first, because it changes storage.** A `dim` change needs a new Qdrant
   collection (`dim` is fixed at creation), then re-embed:
   `QDRANT_COLLECTION... : poetry run python -m app.scripts.migrate_embeddings --target embeddings_multivector_v2 --write`.
   Without the re-embed, old points are not _corrupted_, they are quarantined: the
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
