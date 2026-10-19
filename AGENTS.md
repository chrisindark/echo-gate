# Echo Gate Agent Guide

## Scope

Echo Gate is a Python 3.11+ FastAPI gateway with an independent Next.js 16 UI in `ui/`.
The gateway accepts OpenAI-compatible chat-completion requests and routes to Gemini
(`google-genai`), OpenAI, Groq, OpenRouter, or Ollama. It uses Redis (exact) plus
Qdrant (semantic) caching, Celery for background jobs, and SQLAlchemy with Alembic
(PostgreSQL only; the app connects as a non-owner role so Row-Level Security
enforces per-tenant isolation).

## Documentation Map

Deep reference lives in `docs/`. Read the relevant file before a non-trivial change:

- `docs/ARCHITECTURE.md` — components, module map, the full request lifecycle, the
  cache pipeline, Celery tasks, RLS, and the list of known gaps.
- `docs/API_REFERENCE.md` — every endpoint, request/response shape, headers, errors.
- `docs/CONFIGURATION.md` — every environment variable and its default.
- `docs/DATA_MODEL.md` — Postgres tables, RLS policies, and the Qdrant collection.
- `docs/DEVELOPMENT.md` — setup, commands, tests, migrations, conventions, scripts.

## Plan First

Before editing, identify the affected request path and read the owning controller,
schema, service, and dependency wiring. Keep plans to 3-5 concrete steps; name
files and observable behavior, not generic refactors. Ask only when an API contract,
provider behavior, or persistence decision is genuinely unspecified.

## Architecture

- `app/main.py`: app setup, lifespan, middlewares, and routers. Loads
  `app/.env.<PYTHON_ENV>` when present, then falls back to system environment
  variables.
- `app/core/dependencies.py`: singleton service construction and shutdown. Add new
  shared services here only when FastAPI dependencies need them.
- `app/modules/<feature>/`: keep controllers thin, Pydantic schemas in `*_schema.py`,
  persistence models in `*_model.py`, and business logic in `*_service.py`.
- `app/modules/llm/llm_router_service.py`: cache lookup, semantic matching, provider
  routing, and cache writes. Preserve tenant, model, and request fields that affect
  response correctness when changing cache behavior.
- `app/modules/llm/llm_provider_service.py`: provider adapters for `google-genai`,
  `openai`, `groq`, `openrouter`, and `ollama`.
- `app/worker/celery_app.py` and `tasks.py` in the `gateway_requests`, `qdrant`, and
  `llm_usage` modules: background job definitions. Match the existing task patterns
  when adding work.
- `alembic/`: database migrations. Update models and create a migration for schema
  changes; do not rely on `create_all`.
- `ui/`: separate Next.js app. Follow `ui/AGENTS.md` and `ui/CLAUDE.md`; inspect the
  installed Next.js documentation before changing UI code.

### Module map (where things live)

- `chat/` — public completions API and request/response schema.
- `llm/` — router service (orchestration), provider adapters, cache confidence.
- `embedding/` — dense (`sentence-transformers`) and sparse (BM25) embeddings.
- `redis/`, `qdrant/` — exact + vector cache storage and Qdrant cache collection.
- `intent_classifier/` — intent LLM classification and regex entity extraction.
- `reranking/`, `verifier/` — cross-encoder rerank plus judge/NLI evaluation.
- `gateway_requests/`, `llm_usage/`, `analytics/` — trace log, usage log, aggregation.
- `llm_quota/`, `api_keys/` — Redis-backed quotas and tenant identity. Admin CRUD
  for quota rules is `llm_quota_controller.py` + `llm_quota_rule_service.py`.
- `admin/` — scoped operator credentials (`admin_api_keys`), created only by
  `app/scripts/create_admin_key.py`. Enforced by `AdminApiKeyGuard` in
  `app/core/guards.py`; never mint one over HTTP.
- `health/` — health, liveness, readiness, Celery probes.
- `conversation/` — conversation/message code that is NOT mounted (see Known Gaps).

## Request lifecycle (the path that matters)

`POST /api/v1/chat/completions` → `api_key_auth_optional_guard` → `llm_quota_guard` → `chat_controller` →
`LlmRouterService.generate_completion`:

1. Redis exact match → 2. Qdrant exact match → 3. Qdrant semantic search
   (dense fast-path, else hybrid RRF → intent classification → cross-encoder rerank
   → `CacheConfidenceEvaluator` gate) → 4. provider call with retry/fallback →
   5. background cache write + usage/trace logging.

Routing decisions: `REDIS_EXACT`, `QDRANT_EXACT`, `QDRANT_FAST`, `QDRANT_SEMANTIC`,
`LLM`. Requests that declare `tools` intentionally bypass the semantic cache. See
`docs/ARCHITECTURE.md` for the precise stages, thresholds, and latency fields.

## Key invariants

- Cache entries are tenant-scoped and provider/model-sensitive. The exact cache key
  is a SHA-256 over all fields that affect the answer; the Qdrant query filters on
  `prompt_version`, `model`, `embedding_*`, `cache_key_version`, `cacheable`,
  `expires_at`, and the cache scope.
- A cache miss, or Redis/Qdrant being unavailable, must still return a provider
  response. Never let cache failures raise to the client.
- Cache scope only widens to what the entity extractor allows; sensitive prompts must
  not be stored at `GLOBAL`. Time-sensitive queries (`time_sensitivity >= 0.9`,
  `finance_market`) are not cacheable.
- The app must connect as the non-owner role so Postgres RLS applies; trusted
  background jobs use the system RLS context to cross tenants.
- Blocking ML/Qdrant work goes through `asyncio.to_thread`; provider calls are async
  and go through `app/core/retry.py`. Cache writes and logging are background tasks.
- The public API surface is contract-tested (see Verify); treat changes as additive
  by default.

## Implementation Rules

- Use async I/O for network/provider calls and preserve FastAPI/Pydantic contracts.
- Treat cache entries as tenant-scoped and provider/model-sensitive. A cache miss or
  unavailable Redis/Qdrant must still allow a provider response.
- Do not log API keys, full secrets, or sensitive prompt/response data unnecessarily.
- Keep edits narrowly scoped. Do not reformat unrelated files or overwrite existing
  worktree changes.
- Use absolute imports rooted at `app`; follow Ruff/isort formatting configured in
  `.vscode/settings.json`. `.pre-commit-config.yaml` runs `ruff --fix` and
  `ruff-format` on commit.
- New environment variables must be added to `app/core/config.py` and documented in
  `docs/CONFIGURATION.md`.

## Verify

- Unit tests: `poetry run pytest` (suite in `tests/`, configured in `pyproject.toml`)
- Backend syntax/import check: `poetry run python -m compileall app`
- Infra for local runs: `docker-compose up -d qdrant redis postgres` (scoped; a bare
  `docker-compose up -d` also builds and starts the API container and Celery worker)
- Run the API: `poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
  (`dev.sh` starts the API and the UI together)
- Run migrations when models change: `poetry run alembic upgrade head`
- API contract: `tests/test_openapi_snapshot.py` compares `app.openapi()` against
  `tests/snapshots/openapi.json`. Intentional API changes regenerate the snapshot
  and fail the test; review the `git diff`, confirm additions are additive, then
  commit the regenerated snapshot.
- UI changes: `npm run lint` and `npm run build` from `ui/`

Add focused tests for changed behavior when practical; do not claim integration
verification unless Redis, Qdrant, required environment files, and the selected
provider are available.

## Known Gaps (do not "fix" silently)

These are real, tracked discrepancies — document them, and only change them as an
explicit task:

- The `conversation/` router is defined but **not included** in `app/main.py`, so
  `/api/v1/conversations/*` returns 404. Tables and RLS policies exist.
- The UI hardcodes `http://localhost:8000`, and its sidebar links `/cache` and
  `/settings`, which have no routes.
- `app/.env.example` contains unused keys (`EMBEDDING_DIMENSION`, `EMBEDDING_DEVICE`,
  `CROSS_ENCODER_VERIFIER_MODEL`) and omits `CROSS_ENCODER_RELEVANCE_MODEL`.
- The Celery Beat comments claim 5-minute/1-minute intervals while the coded
  schedules are all 30 seconds; `docker-compose.yml` starts a worker but no Beat.
- **Anonymous chat shares one rate-limit bucket.** `POST /api/v1/chat/completions`
  intentionally permits keyless callers (a supplied `X-API-Key` must still
  resolve). The Redis counter key includes `api_key`, so every keyless request
  lands in `rpm:None:<provider>:<model>:anonymous` and one caller can exhaust the
  default 10 RPM for all anonymous traffic. Per-caller anonymous isolation is a
  known follow-up.
