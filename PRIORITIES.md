# Echo Gate Priorities & Active Work Queue

**Status:** Pre-1.0 Production Hardening (baseline verified 2026-11-05: 534 passed, 17 Postgres-dependent skipped; snapshot gate green).
**Queue extended 2026-11-17:** P4–P6 added for SDK interop, SaaS operations/compliance, and the data-plane/commercial surface. P0–P3 are complete except the open zero-retention/PII item; start at P4.
**Baseline Quality Gate:** `poetry run python -m compileall api/app && poetry run pytest tests/contract/test_openapi_snapshot.py && poetry run pytest tests/`
**Rules:**
1. Work top to bottom. Implement one priority item or clearly bounded slice at a time.
2. Maintain strict non-negotiable invariants: fail-open caching, non-owner Postgres role for RLS, no in-process embeddings.
3. Every API route or schema change must update and verify `tests/contract/test_openapi_snapshot.py`.

---

## Active Priorities

### P0 — Release Hygiene & Deployment Packaging
- [x] **Self-Hosting Docker Compose Profiles** *(2026-11-05)*: `deploy/compose/docker-compose.yml` gates every service behind profiles (`core` = postgres/redis/qdrant/echo-gate/celery_worker/celery_beat, `ui` = web Next.js image from `web/Dockerfile`). `deploy/compose/setup.sh` brings up the inference platform, the profiles, runs `alembic upgrade head` as owner, and ends in `python -m app.scripts.create_admin_key create` (supports `--no-ui`, `--name`, `--scopes`, `--expires-in-days`).
- [x] **Release Tagging & Version Stamping** *(2026-11-05)*: `v0.1.0` tagged (GHCR image `ghcr.io/chrisindark/echo-gate:v0.1.0` with `VERSION` stamped from tag via `Dockerfile` `ARG VERSION`); `scripts/check_versions.py` asserts gateway/web/inference-platform sources and the tag name all agree. GHCR digests recorded in the workflow summary on tag builds (`.github/workflows/docker.yml`), and `CHANGELOG.md` carries the `v0.1.0` section. Semver remains disciplined by the OpenAPI snapshot contract test.
- [x] **Opt-in Anonymous Telemetry** *(2026-11-05)*: `TELEMETRY_ENABLED` (default `false`) + `TELEMETRY_ENDPOINT` (configurable URL, no-op when unset). When enabled, `api/app/core/anonymous_telemetry.py` pings `{version, instance_id, deployment_mode, event}` on startup, then every `TELEMETRY_INTERVAL_SECONDS` (default daily) via lifespan task in `api/app/main.py`. Fail-open: ping failures logged, never affect serving. Config in `api/app/core/config.py` + `api/app/.env.example`; tests in `api/app/core/tests/test_anonymous_telemetry.py`.

### P1 — Production Hardening & Operational Observability
- [x] **Multi-Region Cache Federation Design** *(2026-11-05)*: `docs/MULTI_REGION_CACHE_FEDERATION.md` — each region runs its own full cache stack serving local-first (Redis exact → Qdrant exact → fast-path → hybrid RRF → provider, no cross-region reads on the serving path); high-value entries (value-aware `retention_value` ≥ threshold, `cacheable` only, `GLOBAL`/`TENANT` scope only) replicate asynchronously via Celery (`replicate_cache_entry` + daily high-water sweep) over an admin-scoped ingestion endpoint; Qdrant payload gains additive `region_origin`/`federation_version` with new env knobs (`CACHE_FEDERATION_*`, all default-off) so the schema never adds region-incompatible assumptions. Architecture-only (no behavior change) per plan.
- [x] **Helm Chart for Enterprise Self-Hosted** *(2026-11-05)*: `deploy/helm/echo-gate/` umbrella + `charts/echo-gate-api/` sub-chart (API/worker/beat/migration-Job/Service/Ingress/HPA/PDB/SA). Bundled Postgres/Redis/Qdrant via Bitnami OCI sub-charts (disable for managed services), optional web UI, secret mounting via `existingSecret`/`extraEnvFromSecrets` (cert-manager pattern, no provider keys in values.yaml), Ingress TLS via cert-manager Secret, and a run-migrations-on-deploy Job running `alembic upgrade head` as owner. Pinned images via `global.imageTag`/`appVersion` defaults; readiness gates on `/api/v1/health/ready` (Postgres-only), roll-up on `/api/v1/health/deps`.
- [x] **CI Quality, Security & Contract Gates (Cross-Repo Parity)** *(2026-11-06)*:
  - **Branch Target Fix**: Align `.github/workflows/ci.yml` branch triggers from `main` to `[master, staging]` for push and PR so CI actually runs on `master`.
  - **Doc Capability Drift Guard**: Wire `python scripts/check_doc_capabilities.py` into `.github/workflows/ci.yml` backend quality gate.
  - **Coverage Ratchet Gate**: Configure `pytest-cov` reporting with minimum coverage floor check (`--cov-fail-under=70` in `pyproject.toml` and CI step) and PR HTML report artifact upload.
  - **Secret Scanning Gate**: Add `gitleaks/gitleaks-action@v2` (`fetch-depth: 0`) to `ci.yml` to prevent credential/API key leaks (Groq, OpenAI, Gemini, Postgres/Redis/Qdrant credentials).
  - **Dependency Review & Audit**: Add `actions/dependency-review-action@v4` on PRs and automated vulnerability audits (`pip-audit` for Poetry, `npm audit` for `web/`).
  - **Scheduled Security Cron**: Add weekly scheduled security scan workflow (`.github/workflows/security.yml`, `cron: '0 8 * * 1'`) to catch newly published CVEs against idle dependencies.
  - **Dependabot Automation (Auto-Merge Disabled)**: Add `.github/workflows/dependabot-automation.yml` for automated metadata extraction and security labelling on dependency bumps; keep auto-merge commented out/disabled pending manual review.

### P2 — Commercial Seams & Extensions
- [x] **Event Bus / Webhook Sink** *(2026-11-05)*: `api/app/core/events.py` emits the four typed events (`cache.miss`, `quota.exceeded`, `provider.failure`, `audit.failed`) from their real background-task paths. OSS default is per-type `EVENTS_WEBHOOK_*` URLs; unset type → dropped before dispatch, `EVENTS_ENABLED` kill switch, Celery-or-thread delivery (never inline), fail-open, no prompt text and credential keys stripped. Cloud swaps the sink via `set_event_sink()`. Tests: `core/tests/test_events.py`, `llm/tests/test_event_emission_wiring.py`.
- [x] **Extension Seams** *(2026-11-05)*: `docs/EXTENSION_SEAMS.md` + seams wired (behavior-preserving, OSS defaults = current behavior, OpenAPI snapshot gate confirms no contract change) — `app/core/features.py` (`DEPLOYMENT_MODE` + `register_cloud()`/`is_enabled()`), `app/core/identity.py` (`IdentityProvider`, injected at `TenantContextMiddleware`), `app/core/metering.py` (`MeteringSink`/`NoopMeteringSink`), `app/core/storage.py` (`StorageFactory`), and `/api/v1/billing/*` mounted only when `features.is_enabled("billing")` (placeholder router in `api/app/modules/billing/billing_controller.py` for the private cloud overlay to implement). Tests in `api/app/core/tests/test_extension_seams.py`.

### P3 — Advanced Gateway Features & Protocol Expansion (Post-0.1.0)
- [x] **Prompt Token Optimization Pipeline** *(2026-11-06)*:
  - Implement an opt-in pre-processing pipeline for chat completion requests to reduce token footprint before semantic cache key generation and upstream LLM dispatch.
  - [x] Phase A (Lossless rule-based) *(2026-11-06)*: excessive whitespace collapse (protecting markdown code blocks and inline code), repeated turn/session deduplication (`session-dedup` preserving atomic tool call-response pairings and active prompt), and tool-call JSON array-to-markdown/pipe table serialization (`json-table` with strict size-reduction invariant). Supported via request body `prompt_optimization` or header `X-EchoGate-Prompt-Optimization`, reporting savings via `X-EchoGate-Prompt-Savings`. Tests: `api/app/modules/prompt_optimization/tests/test_prompt_optimizer.py` and `tests/integration/test_prompt_optimization.py`.
  - [x] Phase B (Context & Headroom) *(2026-11-06)*: conversation context reduction (`ccr` retaining initial system prompt and recent $N$ turns while condensing or dropping intermediate turns) and dynamic target model headroom trimming (`headroom-trim` with safety buffer against model context windows). Configured via request body `prompt_optimization` or header `X-EchoGate-Prompt-Optimization: ccr,headroom-trim` and `X-EchoGate-Prompt-Optimization-CCR-Turns`. Tests: `api/app/modules/prompt_optimization/tests/test_prompt_optimizer.py` and `tests/integration/test_prompt_optimization.py`.
  - [x] Phase C (Lossy prompt compression) *(2026-11-06)*: offload heavier compression algorithms (extractive lexical filtering and sentence embedding centroid ranking) to `inference-platform/` over HTTP via `POST /compress` to maintain the non-negotiable invariant of zero in-process embeddings/models in the gateway. Supports `method="lexical"` (token pruning preserving code blocks) and `method="extractive"` (sentence-level centroid ranking), with fail-open resilience. Tests: `inference-platform/tests/test_api.py`, `api/app/modules/inference/tests/test_inference_client.py`, and `tests/integration/test_prompt_optimization.py`.
- [x] **Anthropic Messages Protocol Adapter (`POST /api/v1/messages`)** *(2026-11-06)*:
  - Add native support for the Anthropic Messages API specification so Claude SDKs (Python/TypeScript), Cursor in Claude mode, and Claude Code can connect directly to Echo Gate without client-side translation.
  - Bidirectional payload normalization: map Anthropic system/message blocks to internal representations while maintaining fail-open semantic caching, streaming SSE chunk translation, and usage accounting.
  - Includes `/v1/messages` compatibility alias for SDKs expecting root-level paths.
  - Update OpenAPI snapshot contract gate (`tests/contract/test_openapi_snapshot.py`). Unit tests in `api/app/modules/anthropic/tests/test_anthropic_adapter.py` and integration tests in `tests/integration/test_anthropic_messages.py`.
- [x] **Per-Request Gateway Directives (`cache_enabled`, `cache_ttl`, `retries`)** *(2026-11-07)*:
  - Allow client callers to override gateway caching and retry behavior per request via body (`cache_enabled`, `cache_ttl`, `retries`) and headers (`X-EchoGate-Cache`, `X-EchoGate-Cache-TTL`, `X-EchoGate-Retries`).
  - Support `cache_enabled: false` (cache bypass / force refresh setting `X-Cache: BYPASS`), custom `cache_ttl` in seconds (overriding retention policy calculation in Redis and Qdrant), and per-request `retries` bounds passed to upstream providers.
  - Updates `ChatCompletionRequest` and `AnthropicMessagesRequest` schemas, controllers header resolution, router cache stage bypass/TTL application, and OpenAPI snapshot contract. Integration tests in `tests/integration/test_gateway_directives.py`.
- [x] **Natural Streaming Playback for Cache Hits** *(2026-11-07)*:
  - Replaces monolithic single-chunk streaming output for cache hits with natural word/token chunking and micro-interval pacing (`STREAM_CACHE_PACING_DELAY_MS`) to eliminate UI flickering and support typewriter rendering on streaming frontends.
  - Supports `X-EchoGate-Stream-Pacing: off` client opt-out and custom millisecond pacing via header or `stream_pacing` / `stream_pacing_delay_ms` request body fields.
  - Parity across OpenAI (`/api/v1/chat/completions`) and Anthropic (`/api/v1/messages`) streaming endpoints while preserving exact character identity and OpenAPI contract. Tests in `tests/integration/test_natural_streaming.py`.
- [ ] **Zero-Retention Mode & In-Flight PII Redaction**:
  - Zero-Retention flag (`disable_log: true` or `X-EchoGate-Disable-Log: true`): serve completions with normal quota checks, but completely skip persistence in PostgreSQL (`gateway_requests_log`, `llm_usage_log`) and cache storage (Redis / Qdrant) for HIPAA/GDPR compliance.
  - Pre-routing PII scrubber: scrub sensitive entities (email, phone, credit card with Luhn verification, SSN, IBAN) in Stage 0 before cache key generation and dense/sparse vectorization to prevent cross-tenant/cross-session PII storage.

### P4 — Interop & SDK Drop-In Parity (start here)
Goal: official OpenAI/Anthropic SDKs work by changing only `base_url` + `api_key`.
One PR per item, in this order; every route/schema change updates the OpenAPI snapshot.
Full detail: [`docs/ROADMAP.md#phase-6--interop--sdk-drop-in-parity`](docs/ROADMAP.md#phase-6--interop--sdk-drop-in-parity).

- [x] **Bearer auth parity** *(2026-11-17)* — `Authorization: Bearer <key>` is an alias for `X-API-Key` via the same server-side hash lookup (`core/identity.py`, `core/middlewares.py`, `core/guards.py`). `X-API-Key` wins when both are sent; the Bearer key also feeds the quota bucket and `request.state.api_key`. OpenAPI advertises `BearerAuth`. Tests: `core/tests/test_auth_headers.py`, middleware provenance, end-to-end chat. No caller-supplied tenant trust.
- [x] **`GET /v1/models` + `/api/v1/models`** *(2026-11-17)* — OpenAI-shaped list from `DynamicModelRegistry` incl. aliases and availability, collapsed to one entry per model id; `?include_unavailable=true` (valid `X-Admin-Key`, any scope) exposes disabled and unconfigured entries with flags. Anthropic envelope selected by `anthropic-version`, so both SDKs' `models.list()` work. Tests: `llm/tests/test_models_endpoint.py`; snapshot updated.
- [x] **Error envelope parity** *(2026-11-17)* — OpenAI `{"error": {...}}` for `/chat/completions`, Anthropic envelope for `/messages` (`/v1` alias included) on the non-streaming path; SSE unchanged. App-wide handlers in `core/error_envelopes.py` with exact-path opt-in, so gateway-native routes keep `{"detail": ...}`. `error_code` maps to OpenAI `error.code` and `X-EchoGate-Error-Code`; `Retry-After`/`X-RateLimit-*` pass through; errors carry `X-Gateway-Degradation` (`hard_down` on 5xx). Verification: 16 handler tests plus real openai/anthropic SDK smoke (typed errors, parsed messages); snapshot updated.
- [x] **OpenAI field parity + `extra_body`** *(2026-11-18)* — `ChatCompletionRequest` now accepts `seed`, `logprobs`/`top_logprobs`, `parallel_tool_calls`, `metadata`, `stream_options` (so `stream_options.include_usage` surfaces a final usage chunk on streamed responses), and a size-bounded `extra_body` passthrough (≤64 keys / 64 KiB) that is merged into the upstream payload. Unknown fields are rejected loudly (422) instead of being silently dropped. All new answer-affecting fields participate in the exact cache key (`_generate_cache_key`). Where: `api/app/modules/chat/chat_schema.py`, `api/app/modules/llm/providers/common.py`, `api/app/modules/llm/llm_router_service.py`; snapshot updated.
- [x] **Upstream prompt-cache passthrough** *(2026-11-18)* — Anthropic `cache_control` blocks now survive translation (`ChatMessage.cache_control` private annotation; the Claude adapter re-attaches the block and uses a block-list `system` when annotated). OpenAI/Gemini implicit cache counts are normalized onto `Usage` (`prompt_tokens_details.cached_tokens`; Anthropic `cache_read_input_tokens`/`cache_creation_input_tokens`, with `prompt_tokens` normalized to total input). New `llm_usage_logs` columns `upstream_cached_tokens`, `upstream_cache_read_input_tokens`, `upstream_cache_creation_input_tokens` keep provider-native cache accounting separate from Echo Gate's own cache; the reverse Anthropic envelope reports the cache fields. Costing applies provider multipliers in `core/pricing.py` (OpenAI read 0.5x; Anthropic read 0.1x / write 1.25x; Gemini read 0.25x). Where: `chat/chat_schema.py`, `anthropic/anthropic_adapter.py`, `llm/providers/{claude,gemini}.py`, `llm/llm_router_service.py`, `llm_usage/{model,schema,tasks}.py`, `alembic/versions/a7b8c9d0e1f2_*`. Tests: `llm/tests/test_upstream_prompt_cache.py`.
- [x] **Multimodal preservation** *(2026-11-18)* — OpenAI `image_url` and Anthropic `image`/`document` blocks are preserved in order as private `ChatMessage` parts and translated for OpenAI-compatible, Claude, and Gemini upstreams; unsupported blocks fail loudly. Non-text requests bypass the cache (`X-Cache: BYPASS`) unless `cache_enabled: true` opts in, in which case only exact lookups run with a content-parts digest in the key. Tested image + streaming on both API surfaces: `tests/integration/test_multimodal_preservation.py`.

**P4 gate:** snapshot test + an SDK smoke test (`openai` and `anthropic` clients against the app) + existing suite.

### P5 — SaaS Operations & Compliance
Full detail: [`docs/ROADMAP.md#phase-7--saas-operations--compliance`](docs/ROADMAP.md#phase-7--saas-operations--compliance).

- [ ] **Prometheus `/metrics` + dashboards** — request/latency/error, cache hits by layer, saved cost/time, quota rejects, provider errors, breaker state, queue depth; Grafana dashboard + Helm `ServiceMonitor`/`PrometheusRule`. Startup failure stays fail-open.
- [ ] **Log retention + tenant export/erasure** — Celery beat sweep for `gateway_requests`, `llm_usage`, `shadow_cache_evaluations`, `cache_evaluations`; admin export/erasure covering DB rows and tenant cache entries, dry-run first.
- [ ] **Admin audit trail** — append-only `admin_audit_log` (actor, action, target, request id) for key mint/rotate/revoke, quota CRUD, cache evict/warmup, retention, erasure; admin read endpoint.
- [ ] **Per-tenant fairness** — `max_concurrency` on quota rules + per-tenant semaphore beside global/provider bulkheads; guaranteed permit release on success, failure, fallback, shed, and stream cancel.
- [ ] **Webhook hardening** — HMAC-SHA256 signature + delivery id, bounded retries with jitter, dead-letter persistence; keep unset-URL drop and `EVENTS_ENABLED`.
- [ ] **Idempotency keys** — `Idempotency-Key` on completions/messages: same tenant + key + request hash replays the response and skips double usage; conflicting body returns `409`; document streaming behavior.

### P6 — Data Plane & Commercial Surface
Full detail: [`docs/ROADMAP.md#phase-8--data-plane--commercial-surface`](docs/ROADMAP.md#phase-8--data-plane--commercial-surface).

- [ ] **`/v1/embeddings` + `/v1/rerank`** — proxy through `inference-platform/` (no in-process models), with auth/quota/metering/traces. Primary capability: inference outage → structured `503`, not fail-open.
- [ ] **Per-tenant BYOK provider credentials** — envelope-encrypted at rest, tenant-scoped before env fallback, rotation + revocation, never logged/emitted. Security review required.
- [ ] **Per-tenant model allowlists / plan gating** — enforce effective model set on completions, messages, and `/v1/models`; `403` on violation.
- [ ] **Usage attribution metadata** — bounded request tags persisted on traces/usage, group-by in analytics, never part of identity or cache keys.
- [ ] **Keys/tenant management UI** — key create/list/rotate/revoke + tenant usage summary on the existing admin API; plaintext shown once.
- [ ] **Pricing catalog correctness** — config/DB-backed prices incl. cached read/write rates; unpriced models reported as `cost_known=false` instead of `$0`.

**Not in P4–P6:** Responses API, Batch API, audio/image generation, in-repo MCP (external by decision), SSO/SCIM, hosted portal, Stripe metering, zero-retention/PII (open under P3).

---

## Historical Record
For landed milestone history and architecture plans, see [`docs/ROADMAP.md`](docs/ROADMAP.md).
