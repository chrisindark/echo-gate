# Echo Gate Agent Guide

## Non-Negotiable Invariants
1. **Fail-Open ML & Cache**: If inference platform, Redis, or Qdrant fails, degrade gracefully to direct provider response (`mark_cache_degraded`). Never return 5xx on cache or inference outages.
2. **Tenant & Data Isolation**: Database connects as a non-owner role for PostgreSQL Row-Level Security (RLS). Cache keys are SHA-256 over all response-affecting fields.
3. **No In-Process Embeddings**: All embeddings and reranking route over HTTP to `inference-platform/`. Never import `sentence-transformers` into the gateway.
4. **Known Gaps (Do Not Fix Silently)**: See `docs/limitations.md` and `docs/ARCHITECTURE.md#known-gaps-and-discrepancies`. Do not attempt unapproved refactors.

## Skills & Progressive Disclosure
Instead of reading all docs upfront, load the specific skill matching your task:
- `provider-adapter`: Adding or fixing LLM upstreams (`.agents/skills/provider-adapter/SKILL.md`)
- `cache-pipeline`: Redis, Qdrant hybrid search, and shadow cache (`.agents/skills/cache-pipeline/SKILL.md`)
- `api-contract`: OpenAPI snapshot generation and testing (`.agents/skills/api-contract/SKILL.md`)
- `db-migration`: Alembic migrations and RLS verification (`.agents/skills/db-migration/SKILL.md`)

## Quick Architecture Map
- Public API: `api/app/modules/chat/`
- LLM Router & Cache Orchestration: `api/app/modules/llm/llm_router_service.py`
- Upstream Adapters: `api/app/modules/llm/providers/`
- Background Workers: `api/app/worker/celery_app.py`
- Active Priorities: `PRIORITIES.md`
- Frontend UI: `web/` (see `web/AGENTS.md`)
- Model Server: `inference-platform/` (independent uv project)

## Verification Cheatsheet
- Fast syntax check: `poetry run python -m compileall api/app`
- Unit tests: `poetry run pytest`
- OpenAPI snapshot check: `poetry run pytest tests/contract/test_openapi_snapshot.py`
- UI check: `cd web && npm run lint && npm run build`
