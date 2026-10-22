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
  surfaced as an SSE error event. Covered by `tests/test_streaming_support.py`
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

### ML components

- [x] **Dense embeddings & BM25 sparse**: Dense embeddings `all-MiniLM-L6-v2`
  (sentence-transformers), BM25 sparse embeddings with a persisted model path,
  cross-encoder relevance (`ms-marco-MiniLM-L-6-v2`) and NLI
  (`nli-deberta-v3-small`) models, LLM judge (`llm_usage`-fed evaluation),
  intent classifier, core-query entity extractor.
- [x] **Reranker threshold calibration script** (`app/scripts/calibrate_reranker.py`)
  with precision/recall/F1 sweep.

### UI (`ui/` — Next.js 16, App Router, React 19, Tailwind v4, Radix)

- [x] **Chat playground** (provider/model pickers, temperature/max-tokens,
  structured-output modes, API key, cache-status badges, `?hash=` trace
  reload), traces table + trace detail (latency waterfall, intent metadata,
  evaluation scores), analytics dashboard, admin quota rule management with
  `X-Admin-Key` auth, settings page.
- [x] **Backend URL configurable** via `NEXT_PUBLIC_API_BASE_URL`
  (`ui/src/lib/api.ts`) with a `ui/.env.example`.

### Ops and repo hygiene

- [x] **docker-compose.yml** with qdrant/redis/postgres/api/worker/beat;
  `.dockerignore` keeping secrets and dev tooling out of images.
- [x] **Alembic migrations** with a non-owner app role + owner-role
  `MIGRATION_DATABASE_URL`; OpenAPI snapshot contract test
  (`tests/test_openapi_snapshot.py`); ruff + ruff-format + prettier/eslint
  pre-commit hooks.
- [x] **OSS launch packaging** (`2026-10-21`): AGPL-3.0 `LICENSE`, `NOTICE`,
  README safe-harbor; `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`,
  `CHANGELOG.md`; GitHub Actions (backend + UI CI, Docker buildx/Trivy,
  release-drafter) and issue templates; general `.env` ignore patterns,
  qdrant one-offs under `app/scripts/`.
- [x] **Test suite & documentation set**: ~179-test suite (`tests/`);
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
  - [x] Regenerate OpenAPI snapshot contract (`tests/snapshots/openapi.json`) for conversation routes.
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

- [ ] **Tiered health probes**: keep `/live` process-only and `/ready` minimal;
  ensure a cache blip never evicts gateways from load-balancer membership
  (the `/health/deps` roll-up stays non-gating, used for alerting only).
- [ ] **Per-provider circuit breaker**: add open/half-open/closed state keyed by
  provider (optionally provider+model), tripping on consecutive retryable
  failures from `RETRYABLE_STATUS_CODES` in `app/core/retry.py`. Without it a
  dead provider means every request pays the full
  `LLM_PROVIDER_TIMEOUT_SECONDS` wait and eats a fallback attempt.
- [ ] **Bulkheads and load shedding**: no global or per-provider concurrency cap
  today (only per-API-key quota). Add a max-in-flight semaphore plus
  per-provider caps so one slow provider cannot starve all tenants; shed
  excess load with `429`/`503`.
- [ ] **Explicit degradation contract**: formalize and document the states —
  full → cache-degraded (provider pass-through) → provider-fallback
  (alternate model) → hard-down `503` — and confirm every
  cache/Redis/Qdrant failure path stays fail-open (existing invariant).
- [ ] **Resilient client guidance**: document a retrying client with its own
  breaker and a bypass path (direct provider call or queue-and-replay) for
  when the gateway itself is unavailable.
- [ ] **Config & docs**: add all new knobs to `app/core/config.py` and
  `docs/CONFIGURATION.md`.

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
`evaluate_pending_requests` Celery task are live. That task currently scores the
logged response of every non-error trace in isolation — there is no sampled *fresh-answer*
comparison and no data-driven threshold calibration. Remaining work:

- [ ] **Asynchronous shadow audit trigger** (`LlmRouterService`): on a semantic
  cache hit, roll a small sample probability (p ≈ 0.01–0.02); when selected,
  return the cached response immediately and enqueue a background task that
  (1) requests a fresh completion from the provider for the same prompt,
  (2) dispatches cached-vs-fresh to the judge, (3) persists the result to a
  dedicated audit table.
- [ ] **Comparative lightweight judge**: extend the judge with the zero-shot
  equivalence prompt (respond `YES`/`NO` for semantic equivalence, run on a
  fast low-cost model so audit overhead stays ~0.01–0.05% of total LLM
  spend), so the judge can catch silent false-positive hits, not just score
  cached answers in isolation.
- [ ] **Audit ground-truth store**: a dedicated Postgres table
  (`cache_evaluations`): `request_id`, `tenant_id`, `model_name`,
  `query_text`, `cached_answer`, `fresh_answer`, `intent`,
  `vector_similarity`, `rerank_score`, `is_equivalent`, `cache_point_id`,
  `redis_cache_key`, `latency_saved_ms`, `created_at`. Alembic migration via
  the standard flow.
- [ ] **Instant eviction (kill switch)**: when an audited hit is judged
  non-equivalent, immediately delete the Qdrant point
  (`llm_cache_service.delete_point`) and the Redis key so no subsequent
  request can hit the poisoned/stale entry.
- [ ] **Periodic intent threshold calibration (conformal risk control)**: a
  scheduled batch job (or admin CLI) analyzes the audit table grouped by
  intent and computes an empirical threshold quantile guaranteeing an error
  rate ≤ α (e.g. ≤ 2%) per intent, applying it as a Redis override
  (`cache:thresholds:{intent}`) rather than online mutation. Note:
  `app/scripts/calibrate_reranker.py` currently sweeps from a static test
  dataset, not live audit records.
- [ ] **In-domain dataset export**: export `(query_text, cached_answer, is_equivalent)`
  triples as they accumulate to fine-tune an in-domain cross-encoder reranker.

---

### Phase 3 — Performance and cost of serving

- [ ] **High-performance model serving**: strip `sentence-transformers`,
  `transformers`, and `torch` from the core gateway container (image from
  ~5GB+ toward <300MB, faster startup, horizontal autoscaling); offload
  embeddings and cross-encoder reranking to a dedicated inference server
  (TEI or Infinity) with GPU/dynamic batching; refactor `CrossEncoderService`
  into an async HTTP client (`httpx.AsyncClient`) calling the inference
  server (note `RerankerService` imports it, so both consumers must move off
  the in-process model).
- [ ] **Cost-aware cache retention (vCache-style)**: record generation metrics
  (latency seconds, token usage, model tier) in cache-entry metadata; weight
  retention to protect expensive responses (slow reasoning models, complex
  RAG outputs) from eviction while aggressively evicting cheap ones (short
  chitchat, greetings) — maximizing latency/dollar saved per MB of cache.

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

- [ ] `app/core/features.py` — feature-flag registry
  (`DEPLOYMENT_MODE=oss|cloud`); no runtime license logic in the OSS build.
- [ ] **Identity protocol**: extract sign-in from the current `X-API-Key`/admin-key
  guards into an `IdentityProvider` protocol; OSS keeps
  `ApiKeyIdentityProvider`, cloud registers `OidcIdentityProvider` plus a
  session portal. `TenantContextMiddleware` is the natural injection point.
- [ ] **Metering export**: emit typed usage events (from `_log_usage`) to a
  configurable sink; OSS defaults to a `noop` sink, the cloud installs a
  Stripe recorder.
- [ ] **Storage/broker hardening**: abstract the `redis_service`/`qdrant`
  constructors for managed-service connection parameters (TLS, IAM auth,
  retry policy) — the cloud needs this more than self-hosters.
- [ ] **Admin surface**: keep `/api/v1/admin/*`; cloud registers `billing:*`
  routes only when the ee package is present.

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

- [ ] `deploy/compose/` with profiles (`core` vs `ui`), one-liner quickstart
  ending in `python -m app.scripts.create_admin_key create`.
- [ ] Helm chart for enterprise self-hosted (cert-manager-friendly; values for
  `PYTHON_ENV=production`, pinned image tags, run-migrations-on-deploy job).
- [ ] Versioned releases: Git tags, GHCR image digests, `CHANGELOG.md`, semver
  disciplined by the existing OpenAPI snapshot contract test.
- [ ] Opt-in telemetry (`TELEMETRY_ENABLED`, default off; version + instance
  id ping), documented in `docs/`.

**Sequencing within this phase**: extension seams (no behavior change,
~4 focused PRs) → OSS launch bound to Phase 0 → cloud distribution
(metering first, then portal, then SSO) → enterprise self-hosted (Helm +
support playbook) last.
