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
- [ ] **Multi-Region Cache Federation Design**: Document cache federation architecture where each region runs its own cache stack (local-first read → provider, async replication of high-value entries across regions via Celery).
- [ ] **Helm Chart for Enterprise Self-Hosted**: Kubernetes Helm chart with cert-manager support, secret mounting, and run-migrations-on-deploy job.

### P2 — Commercial Seams & Extensions
- [ ] **Extension Seams**: Clean decoupling of cloud billing interfaces (`/api/v1/billing/*`) without cluttering open-source core.

---

## Historical Record
For landed milestone history and architecture plans, see [`docs/ROADMAP.md`](docs/ROADMAP.md).
