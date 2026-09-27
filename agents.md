# Echo Gate Agent Guide

## Scope

Echo Gate is a Python 3.11+ FastAPI gateway with an independent Next.js 16 UI in `ui/`.
The gateway accepts OpenAI-compatible chat-completion requests, routes to Ollama,
Gemini, or OpenAI, and uses Redis (exact) plus Qdrant (semantic) caching.

## Plan First

Before editing, identify the affected request path and read the owning controller,
schema, service, and dependency wiring. Keep plans to 3-5 concrete steps; name
files and observable behavior, not generic refactors. Ask only when an API contract,
provider behavior, or persistence decision is genuinely unspecified.

## Architecture

- `app/main.py`: app setup, lifespan, middleware, and routers. It requires
  `app/.env.<PYTHON_ENV>` before startup.
- `app/core/dependencies.py`: singleton service construction and shutdown. Add new
  shared services here only when FastAPI dependencies need them.
- `app/modules/<feature>/`: keep controllers thin, Pydantic schemas in `*_schema.py`,
  persistence models in `*_model.py`, and business logic in `*_service.py`.
- `app/modules/llm/llm_router_service.py`: cache lookup, semantic matching, provider
  routing, and cache writes. Preserve tenant, model, and request fields that affect
  response correctness when changing cache behavior.
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
  `.vscode/settings.json`.

## Verify

- Backend syntax/import check: `poetry run python -m compileall app`
- Run the API: `docker-compose up -d` then
  `poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
- Run migrations when models change: `poetry run alembic upgrade head`
- UI changes: `npm run lint` and `npm run build` from `ui/`

There is no committed automated test suite. Add focused tests for changed behavior
when practical; do not claim integration verification unless Redis, Qdrant, required
environment files, and the selected provider are available.
