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

### P2 — Commercial Seams & Extensions
- [x] **Event Bus / Webhook Sink** *(2026-11-05)*: `api/app/core/events.py` emits the four typed events (`cache.miss`, `quota.exceeded`, `provider.failure`, `audit.failed`) from their real background-task paths. OSS default is per-type `EVENTS_WEBHOOK_*` URLs; unset type → dropped before dispatch, `EVENTS_ENABLED` kill switch, Celery-or-thread delivery (never inline), fail-open, no prompt text and credential keys stripped. Cloud swaps the sink via `set_event_sink()`. Tests: `core/tests/test_events.py`, `llm/tests/test_event_emission_wiring.py`.
- [x] **Extension Seams** *(2026-11-05)*: `docs/EXTENSION_SEAMS.md` + seams wired (behavior-preserving, OSS defaults = current behavior, OpenAPI snapshot gate confirms no contract change) — `app/core/features.py` (`DEPLOYMENT_MODE` + `register_cloud()`/`is_enabled()`), `app/core/identity.py` (`IdentityProvider`, injected at `TenantContextMiddleware`), `app/core/metering.py` (`MeteringSink`/`NoopMeteringSink`), `app/core/storage.py` (`StorageFactory`), and `/api/v1/billing/*` mounted only when `features.is_enabled("billing")` (placeholder router in `api/app/modules/billing/billing_controller.py` for the private cloud overlay to implement). Tests in `api/app/core/tests/test_extension_seams.py`.

---

## Historical Record
For landed milestone history and architecture plans, see [`docs/ROADMAP.md`](docs/ROADMAP.md).
