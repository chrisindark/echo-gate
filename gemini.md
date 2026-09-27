# Echo Gate Development Context

Plan compactly and from evidence. Read the relevant controller, schema, service, and
dependency wiring before changing code. Describe 3-5 concrete steps with affected
files and acceptance criteria; avoid speculative architecture work.

## System

Echo Gate is a Python 3.11+ FastAPI LLM gateway. It exposes OpenAI-compatible chat
completions, routes requests to Ollama, Gemini, or OpenAI, and caches completions via
Redis exact matches and Qdrant semantic matches with embeddings/reranking.

## Where To Work

- App/lifespan/routers: `app/main.py`
- Shared dependencies: `app/core/dependencies.py`
- API modules: `app/modules/<feature>/`
- Cache and provider orchestration: `app/modules/llm/llm_router_service.py`
- Provider adapters: `app/modules/llm/llm_provider_service.py`
- Relational models and migrations: `app/modules/*/*_model.py`, `alembic/`
- Frontend: `ui/` (obey its local `AGENTS.md` and `CLAUDE.md`)

## Guardrails

- Startup requires `app/.env.<PYTHON_ENV>`; do not change environment loading without
  checking deployment consequences.
- Maintain Pydantic/OpenAI-compatible API contracts and async behavior.
- For cache changes, preserve tenant/user isolation and model/request correctness;
  Redis or Qdrant errors must degrade to a provider call.
- Add a migration for relational schema changes. Keep controllers thin, services
  focused, and imports rooted at `app`.
- Preserve existing worktree changes, avoid unrelated refactors, and never log keys
  or other secrets.

## Validation

- `poetry run python -m compileall app`
- `poetry run alembic upgrade head` for migration work
- `docker-compose up -d` for Redis and Qdrant
- `npm run lint` and `npm run build` from `ui/` for frontend work

No automated test suite is committed. Add focused tests when feasible and report
exactly what was verified, including unavailable services or environment files.
