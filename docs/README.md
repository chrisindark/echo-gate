# Echo Gate Documentation

This directory is the detailed reference for the Echo Gate LLM gateway. The root
[`README.md`](../README.md) is the quick-start; these files are the deep dive.

| Document | What it covers |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System components, module map, the full request lifecycle, the multi-level cache pipeline, background workers, and multi-tenant isolation. |
| [API_REFERENCE.md](API_REFERENCE.md) | Every HTTP endpoint: method, path, request/response shapes, headers, and error behavior. |
| [CONFIGURATION.md](CONFIGURATION.md) | Every environment variable, its default, and how it changes behavior. |
| [DATA_MODEL.md](DATA_MODEL.md) | Postgres tables, Row-Level Security policies, and the Qdrant cache collection schema. |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Local setup, test/build/lint commands, migrations, scripts, and contribution conventions. |
| [ROADMAP.md](ROADMAP.md) | Current plan of record: features already landed, and the planned work (resilience, cache-quality loop, serving performance, commercialization) in development order. |

## What Echo Gate is

Echo Gate is an OpenAI-compatible chat-completion gateway. It accepts
`/api/v1/chat/completions` requests and routes them to one of several LLM
providers, but the reason it exists is the **multi-level semantic cache**: instead
of always paying for a model call, it first tries to serve a previous, semantically
equivalent response from Redis and Qdrant, and it verifies that the match is safe
before returning it.

```text
Client (OpenAI-compatible)
        │  POST /api/v1/chat/completions  (X-API-Key)
        ▼
┌─────────────────────────────────────────────────────────────┐
│ Echo Gate (FastAPI)                                          │
│  Redis exact → Qdrant exact → Qdrant fast-path → hybrid RRF  │
│  → intent classification → cross-encoder rerank → confidence  │
│  → provider (Gemini/OpenAI/Groq/OpenRouter/Ollama) or cache  │
└─────────────────────────────────────────────────────────────┘
        │ async background
        ▼
Redis + Qdrant (cache write) · Postgres (trace + usage) · Celery (eval, eviction)
```

## Fast orientation for an agentic harness

- **Entry point:** `api/app/main.py` wires middleware and routers; the interesting
  request path is `api/app/modules/llm/llm_router_service.py::generate_completion`.
- **Cache:** Redis (`api/app/modules/redis/redis_service.py`) plus a Qdrant collection
  named `embeddings_multivector` (`api/app/modules/qdrant/llm_cache_collection_service.py`).
- **Providers:** `api/app/modules/llm/llm_provider_service.py`.
- **Persistence:** SQLAlchemy models in `api/app/modules/*/*_model.py`, migrated by
  the single Alembic baseline in `alembic/versions/`.
- **Background work:** `api/app/worker/celery_app.py` and `tasks.py` in the
  `gateway_requests`, `llm_usage`, and `qdrant` modules.
- **UI:** independent Next.js app in `web/` (chat playground, traces, analytics).
- **Known gaps** (documented, not yet fixed) are listed in
  [ARCHITECTURE.md → Known gaps](ARCHITECTURE.md#known-gaps-and-discrepancies).
