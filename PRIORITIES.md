# Echo Gate Priorities & Active Work Queue

**Status:** Pre-1.0 Production Hardening
**Baseline Quality Gate:** `poetry run python -m compileall api/app && poetry run pytest tests/test_openapi_snapshot.py && poetry run pytest tests/`
**Rules:**
1. Work top to bottom. Implement one priority item or clearly bounded slice at a time.
2. Maintain strict non-negotiable invariants: fail-open caching, non-owner Postgres role for RLS, no in-process embeddings.
3. Every API route or schema change must update and verify `tests/test_openapi_snapshot.py`.

---

## Active Priorities

### P0 — Release Hygiene & Deployment Packaging
- [ ] **Self-Hosting Docker Compose Profiles**: Clean `deploy/compose/docker-compose.yml` with profiles (`core` vs `ui`) and an automated setup script ending in `python -m app.scripts.create_admin_key create`.
- [ ] **Release Tagging & Version Stamping**: Git tags, GHCR image digests, and semver disciplined by the OpenAPI snapshot contract test.
- [ ] **Opt-in Anonymous Telemetry**: Optional `TELEMETRY_ENABLED` (default `false`) with instance ping.

### P1 — Production Hardening & Operational Observability
- [ ] **Multi-Region Cache Federation Design**: Document cache federation architecture where each region runs its own cache stack (local-first read → provider, async replication of high-value entries across regions via Celery).
- [ ] **Helm Chart for Enterprise Self-Hosted**: Kubernetes Helm chart with cert-manager support, secret mounting, and run-migrations-on-deploy job.

### P2 — Commercial Seams & Extensions
- [ ] **Extension Seams**: Clean decoupling of cloud billing interfaces (`/api/v1/billing/*`) without cluttering open-source core.

---

## Historical Record
For landed milestone history and architecture plans, see [`docs/ROADMAP.md`](docs/ROADMAP.md).
