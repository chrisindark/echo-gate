# Echo Gate Instructions

Work as a concise planner before implementation. First trace the request through its
controller, schema, service, and dependency registration. Propose no more than five
file-specific steps, then implement only the necessary ones.

## Repository Map

- FastAPI backend: `app/`; entry point: `app/main.py`.
- Dependency lifecycle: `app/core/dependencies.py`.
- Feature layout: `app/modules/<feature>/` with controller, schema, model, and/or
  service files.
- LLM orchestration and cache policy: `app/modules/llm/llm_router_service.py`.
- Providers: `app/modules/llm/llm_provider_service.py`.
- Exact cache: Redis; semantic cache: Qdrant plus embeddings and reranking.
- Database migrations: `alembic/`; make a migration for relational schema changes.
- Next.js 16 frontend: `ui/`; its local `AGENTS.md` and `CLAUDE.md` override this
  file for UI work.

## Constraints

- `app/main.py` loads required `app/.env.<PYTHON_ENV>` at import time.
- Preserve OpenAI-compatible request/response schemas and async network paths.
- Cache correctness depends on tenant/user, provider/model, and response-affecting
  request fields. Cache or infrastructure failures must not prevent a provider call.
- Keep controllers thin and put business logic in services.
- Do not expose secrets or make unrelated cleanup changes.

## Commands

Use Poetry for backend commands:

`poetry run python -m compileall app`

`poetry run alembic upgrade head`

`poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`

Use `docker-compose up -d` for Redis and Qdrant. For UI work, run `npm run lint` and
`npm run build` inside `ui/`. No automated test suite is committed, so state clearly
which checks were run and which integration dependencies were unavailable.
