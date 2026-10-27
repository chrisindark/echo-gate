# Echo Gate Agent Guide

## Scope

Echo Gate is a Python 3.11+ FastAPI gateway with an independent Next.js 16 UI in `web/`.
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
- `docs/ROADMAP.md` — plan of record: landed features and planned work in
  development order. Read before proposing new work.

## Plan First

Before editing, identify the affected request path and read the owning controller,
schema, service, and dependency wiring. Keep plans to 3-5 concrete steps; name
files and observable behavior, not generic refactors. Ask only when an API contract,
provider behavior, or persistence decision is genuinely unspecified.

## Architecture

- `api/app/main.py`: app setup, lifespan, middlewares, and routers. Loads
  `api/app/.env.<PYTHON_ENV>` when present, then falls back to system environment
  variables.
- `api/app/core/dependencies.py`: singleton service construction and shutdown. Add new
  shared services here only when FastAPI dependencies need them.
- `api/app/modules/<feature>/`: keep controllers thin, Pydantic schemas in `*_schema.py`,
  persistence models in `*_model.py`, and business logic in `*_service.py`.
- `api/app/modules/llm/llm_router_service.py`: cache lookup, semantic matching, provider
  routing, and cache writes. Preserve tenant, model, and request fields that affect
  response correctness when changing cache behavior.
- `api/app/modules/llm/llm_provider_service.py`: provider adapters for `google-genai`,
  `openai`, `groq`, `openrouter`, and `ollama`.
- `api/app/worker/celery_app.py` and `tasks.py` in the `gateway_requests`, `qdrant`, and
  `llm_usage` modules: background job definitions. Match the existing task patterns
  when adding work.
- `alembic/`: database migrations. Update models and create a migration for schema
  changes; do not rely on `create_all`.
- `web/`: separate Next.js app. Follow `web/AGENTS.md` and `web/CLAUDE.md`; inspect the
  installed Next.js documentation before changing UI code.

### Module map (where things live)

- `chat/` — public completions API and request/response schema.
- `llm/` — router service (orchestration), provider adapters, cache confidence.
- `embedding/` — dense embeddings (remote via `inference-platform/`, or
  `sentence-transformers` in-process) and sparse (BM25, always local).
- `inference/` — HTTP client for the shared inference platform (`/embeddings`,
  `/rerank`, `/classify`), role routing, and error classification.
- `redis/`, `qdrant/` — exact + vector cache storage and Qdrant cache collection.
- `intent_classifier/` — intent LLM classification and regex entity extraction.
- `reranking/`, `verifier/` — cross-encoder rerank plus judge/NLI evaluation.
- `gateway_requests/`, `llm_usage/`, `analytics/` — trace log, usage log, aggregation.
- `shadow_cache/` — shadow-mode evaluation rows, readiness summary, and the
  `/api/v1/shadow-cache` read API.
- `llm_quota/`, `api_keys/` — Redis-backed quotas and tenant identity. Admin CRUD
  for quota rules is `llm_quota_controller.py` + `llm_quota_rule_service.py`.
- `admin/` — scoped operator credentials (`admin_api_keys`), created only by
  `python -m app.scripts.create_admin_key`. Enforced by `AdminApiKeyGuard` in
  `app.core.guards`; never mint one over HTTP.
- `health/` — health, liveness, readiness, Celery probes.
- `conversation/` — conversation/message model, service, controller (mounted in `main.py`).

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
- `CACHE_MODE` is the serving switch (`disabled` / `shadow` / `active`); only
  `active` may read from the cache. Serving gates must go through
  `Config.cache_layer_enabled(layer)`; in `shadow` mode the provider always answers
  and `_background_shadow_evaluate` replays the pipeline with `force=True`, writing
  the corpus and `shadow_cache_evaluations` out of band.
- A cache miss, or Redis/Qdrant being unavailable, must still return a provider
  response. Never let cache failures raise to the client.
- Cache scope only widens to what the entity extractor allows; sensitive prompts must
  not be stored at `GLOBAL`. Time-sensitive queries (`time_sensitivity >= 0.9`,
  `finance_market`) are not cacheable.
- The app must connect as the non-owner role so Postgres RLS applies; trusted
  background jobs use the system RLS context to cross tenants.
- Inference-platform calls are fail-open: an unreachable engine degrades to the
  provider path (`mark_cache_degraded("inference_unavailable")`), never to a 5xx.
  A refused connection is not retried; timeouts, throttling, and 5xx are.
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
- In-process models are an optional extra (`poetry install --extras local-models`);
  without it, model roles come from `INFERENCE_BASE_URL`. Do not move
  `sentence-transformers` back into the main dependencies — it pulls ~5GB of
  CUDA libraries into every image (0.71GB image becomes 9.4GB).
- Backend syntax/import check: `poetry run python -m compileall api/app`
- Infra for local runs: `docker compose -f deploy/compose/docker-compose.yml up -d qdrant redis postgres`
- Run the API: `poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
  (`dev.sh` starts the API and the UI together)
- Run migrations when models change: `poetry run alembic upgrade head`
- API contract: `tests/test_openapi_snapshot.py` compares `app.openapi()` against
  `tests/snapshots/openapi.json`. Intentional API changes regenerate the snapshot
  and fail the test; review the `git diff`, confirm additions are additive, then
  commit the regenerated snapshot.
- UI changes: `npm run lint` and `npm run build` from `web/`

Add focused tests for changed behavior when practical; do not claim integration
verification unless Redis, Qdrant, required environment files, and the selected
provider are available.

## Known Gaps (do not "fix" silently)

Real, tracked discrepancies — document them, and only change them as an
explicit task:

- None currently tracked. The former gaps (`conversation/` router unmounted,
  anonymous shared rate-limit bucket, `.env.example` drift, Celery Beat
  schedule mismatch) have been resolved; the date-stamped record lives in
  `docs/ROADMAP.md`. Add new discrepancies here instead of fixing them in
  passing.
