# Echo Gate Agent Guide

## Scope

Echo Gate is a Python 3.11+ FastAPI gateway with an independent Next.js 16 UI in `ui/`.
The gateway accepts OpenAI-compatible chat-completion requests and routes to Gemini
(`google-genai`), OpenAI, Groq, OpenRouter, or Ollama. It uses Redis (exact) plus
Qdrant (semantic) caching, Celery for background jobs, and SQLAlchemy with Alembic
(PostgreSQL only; the app connects as a non-owner role so Row-Level Security
enforces per-tenant isolation).

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

## Verify

- Unit tests: `poetry run pytest` (suite in `tests/`, configured in `pyproject.toml`)
- Backend syntax/import check: `poetry run python -m compileall app`
- Infra for local runs: `docker-compose up -d qdrant redis postgres` (scoped; a bare
  `docker-compose up -d` also builds and starts the API container and Celery worker)
- Run the API: `poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
  (`dev.sh` starts the API and the UI together)
- Run migrations when models change: `poetry run alembic upgrade head`
- UI changes: `npm run lint` and `npm run build` from `ui/`

Add focused tests for changed behavior when practical; do not claim integration
verification unless Redis, Qdrant, required environment files, and the selected
provider are available.
