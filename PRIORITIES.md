# Echo Gate Priorities & Active Work Queue

**Status:** Pre-1.0 Production Hardening (baseline verified 2026-11-05: 534 passed, 17 Postgres-dependent skipped; snapshot gate green)
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
- [ ] **Soft-Steering Guardrails & In-Flight PII Redaction**:
  - Dual-mode guardrails: support both hard-blocking (`block` + audit log on violation) and soft-steering (`inject` mode: appending dynamic system instructions for model self-enforcement without request abortion).
  - Pre-routing PII scrubber: scrub sensitive entities (email, phone, credit card, SSN, IBAN, and custom regex patterns) *before* cache lookup or upstream transmission to prevent sensitive data leakage into Redis, Qdrant vectors, or external provider logs.
- [ ] **Dynamic Routing Profiles & A/B Experiments**:
  - Introduce reusable routing profiles (e.g. `quality`, `cheap`, `fast`, `code`) bundling candidate model priorities, fallback chains, and timeout budgets.
  - A/B testing engine: split traffic across router variants (models, prompt versions, or providers) with configurable rotation (`weighted`, `round-robin`, or `sticky` via `X-Session-ID`, `user` identity, or hashed conversation prefix).

---

## Historical Record
For landed milestone history and architecture plans, see [`docs/ROADMAP.md`](docs/ROADMAP.md).
