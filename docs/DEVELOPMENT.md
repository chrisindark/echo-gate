# Development

## Prerequisites

- Python **3.11+** and [Poetry](https://python-poetry.org/)
- Node.js 20+ (for `ui/`)
- Docker + Docker Compose (Postgres, Redis, Qdrant)
- Ollama on `:11434` if you use local models or the default intent classifier /
  embedding setup

## First-time setup

```bash
# 1. Environment file
cp app/.env.example app/.env.local       # then edit keys/URLs

# 2. Infrastructure (Postgres, Redis, Qdrant)
docker-compose up -d postgres redis qdrant

# 3. Backend dependencies
poetry install

# 4. Schema (creates tables, the app role, and RLS policies) via the OWNER creds
poetry run alembic upgrade head

# 5. Create an API key (after the API is running)
curl -X POST http://localhost:8000/api/v1/admin/api-keys/ \
  -H "Content-Type: application/json" \
  -d '{"name": "local-dev", "tenant_id": "acme", "user_id": "dev"}'
```

Remember: the cache layers default to `"false"`. Set
`USE_REDIS_EXACT_MATCHING=true`, `USE_QDRANT_EXACT_MATCHING=true`, and
`USE_QDRANT_SEMANTIC_MATCHING=true` in the env file to exercise caching. See
[CONFIGURATION.md](CONFIGURATION.md).

## Running

```bash
# API only
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# API + UI together
./dev.sh

# Celery worker
poetry run celery -A app.worker.celery_app worker --loglevel=info

# Celery beat (scheduled tasks; not started by docker-compose)
poetry run celery -A app.worker.celery_app beat --loglevel=info

# UI only
cd ui && npm install && npm run dev   # http://localhost:3000
```

Interactive API docs: `http://localhost:8000/docs`.

## Tests

```bash
poetry run pytest                 # config in pyproject.toml: asyncio_mode=auto, testpaths=tests
poetry run python -m compileall app   # syntax/import sanity check
```

Existing suites:

| File | Covers |
| --- | --- |
| `tests/test_api_keys_unit.py` | Key hashing determinism, prefix, entropy, empty-key resolution. |
| `tests/test_health.py` | `/health/live`, `/ready`, `/deps`, `/worker` healthy and degraded paths. |
| `tests/test_logging_and_correlation.py` | JSON formatter, logger setup, latency decorator, correlation-ID middleware. |
| `tests/test_rls.py` | RLS stamping/isolation, fail-closed default, cross-tenant insert rejection, API-key resolution. (Requires a live Postgres.) |
| `tests/test_tenant_middleware.py` | Identity propagation and no context leakage between requests. |
| `tests/test_tools_support.py` | Tool/tool-call schema, cache-key uniqueness, prompt prep, semantic-cache bypass with tools, Gemini translation. |
| `tests/test_openapi_snapshot.py` | API contract gate (see below). |

### API contract snapshot

`tests/test_openapi_snapshot.py` compares `app.openapi()` against the committed
`tests/snapshots/openapi.json`. Any structural API change makes the test fail and
regenerates the snapshot so the change appears in `git diff`; the failure report
classifies additions (safe) versus removals/modifications (breaking). When you
intentionally change the API, review the diff, confirm additions are additive, and
commit the regenerated snapshot. To discard an accidental run,
`git checkout -- tests/snapshots/openapi.json`.

Many tests import `app.main`, so they need the same env the app does; integration
tests (`test_rls.py`) need the live infrastructure and the non-owner app role.

## Migrations

There is currently a single baseline revision. When models change:

```bash
# Author a new revision (owner DB URL is used by alembic)
poetry run alembic revision --autogenerate -m "add <table>"
poetry run alembic upgrade head
```

The baseline provisions the `echo_gate_app` role and enables RLS on the tenant,
user, and quota tables. If you add a tenant-scoped table, add matching RLS
enable/policy statements to the migration — mirror the existing `_TENANT_POLICY`,
`_QUOTA_POLICY`, and `_USER_POLICY` templates in
`alembic/versions/3f9c1a7b2d40_baseline_postgres_rls.py`. For a schema change that
alters the API, also regenerate the OpenAPI snapshot (above).

## Utility scripts

| Script | Purpose |
| --- | --- |
| `app/scripts/create_admin_key.py` | **The only way to mint an admin API key.** Subcommands `create --name --scopes [--expires-in-days]`, `list`, `revoke --id`. Prints the plaintext (`ega_...`) once. Run with `PYTHON_ENV=local`. |
| `app/scripts/run_tests.py` | Sends the `TEST_DATASET` prompts to the gateway and writes `run_test_results.csv`. Env: `ECHO_GATE_URL`. |
| `app/scripts/seed_prompts.py` | Seeds prompts and checks cache-threshold behavior; writes `send_prompt_results.csv`. Env: `ECHO_GATE_URL`, `ECHO_GATE_RESULTS_CSV`. |
| `app/scripts/migrate_embeddings.py` | Re-embeds points between collections (`embeddings_multivector` → `..._v2`). Hardcodes `QDRANT_URL` and `EMBEDDING_DIM` — edit before use. |
| `app/scripts/test_dataset.py` | The labelled prompt dataset (canonical id + `expected_match`) used by the runners. |
| `app/scripts/calibrate_reranker.py` | Replays `TEST_DATASET` through `RerankerService` offline (no Redis/Qdrant/API) and sweeps acceptance thresholds, writing `reranker_calibration.{csv,json}` with precision/recall/F1 and a recommended threshold. Flags: `--limit`, `--max-candidates`, `--scoring-mode`, `--score-source`. |
| `app/scripts/inspect_semantic_cache.py` | Read-only report over `gateway_requests.score_breakdown` (offline sweeps cannot exercise the intent/metadata factors). Flags near-boundary rejections: candidates whose `base_score` cleared the intent's high threshold but whose bounded factors dropped the final score below it, with per-field `metadata_comparisons`. Flags: `--limit`, `--boundary-margin`, `--json`. Run with `PYTHON_ENV=local` so the DB URL is loaded. |
| `create_qdrant.py` | Re-initializes the Qdrant collection via the DI container. |
| `check_qdrant.py` | Scrolls the first 5 points to inspect scope/tenant/user. |
| `wipe_qdrant.py` | Drops the `embeddings_multivector` collection. |

## UI

`ui/` is a separate Next.js 16 app. Read `ui/AGENTS.md` (and the installed Next.js
docs under `ui/node_modules/next/dist/docs/`) before editing. Key facts:

- Pages: `/` (chat playground), `/traces`, `/traces/[id]`, `/analytics`, `/quota`
  (admin; needs an `X-Admin-Key`).
- `ui/src/components/app-sidebar.tsx` also links `/cache` and `/settings`, which
  have no routes. `ui/README.md` and [ARCHITECTURE.md](ARCHITECTURE.md#known-gaps-and-discrepancies)
  note this.
- The UI hardcodes `http://localhost:8000` for API calls (no env var yet).

```bash
cd ui
npm run lint
npm run build
```

## Conventions

- **Structure:** controllers stay thin; Pydantic models in `*_schema.py`, SQLAlchemy
  models in `*_model.py`, logic in `*_service.py`.
- **Imports:** absolute, rooted at `app` (e.g. `from app.core.config import config`).
- **Async:** use async I/O for provider/network calls; wrap blocking ML/Qdrant work
  with `asyncio.to_thread` and run cache writes/logging as background tasks.
- **Formatting:** Ruff + ruff-format (line length 88, target py311). `.pre-commit-config.yaml`
  runs `ruff --fix` and `ruff-format` on commit. Import order follows isort.
- **Secrets:** never log API keys or full prompt/response payloads unnecessarily.
- **Cache correctness:** when changing the cache, preserve the tenant/model/request
  fields that affect response correctness (see the cache key in
  `_generate_cache_key` and the Qdrant filter in `evaluate_semantic_cache`), and
  ensure a cache miss or unavailable Redis/Qdrant still yields a provider response.
- **New settings:** add to `app/core/config.py`, document it in
  `docs/CONFIGURATION.md` and `app/.env.example`.

## Debugging tips

- `GET /api/v1/health/deps` isolates which backing service is unhealthy, and is the
  endpoint to alert on — it never returns 503. Use `/api/v1/health/ready` only for
  the gating question ("can this replica serve correctly?"), which is Postgres alone.
- `POST /api/v1/chat/completions/semantic-search` (and `/exact-search`) exercise the
  cache without a provider call.
- `GET /api/v1/gateway-requests/?search=...` finds a trace; the detail view exposes
  `latency_breakdown` and evaluation scores.
- Correlation IDs (`X-Correlation-ID`/`X-Request-ID`) thread a request through the
  structured logs.
