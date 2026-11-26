# Echo Gate LLM Gateway

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)

**Echo Gate** is an asynchronous LLM API gateway and observability platform built with
**FastAPI** (Python 3.11+), with an independent **Next.js 16** frontend in `web/`.

It accepts both OpenAI-compatible `chat/completions` and Anthropic-compatible
`messages` requests and routes them across local and cloud providers (**Ollama**,
**Google GenAI / Gemini**, **OpenAI**, **Groq**, **OpenRouter**, **Claude**). Its core
capability is a **Multi-Level Semantic Caching & Verification Pipeline** — Redis,
Qdrant hybrid search, cross-encoder reranking, intent classification, and
asynchronous judge evaluations — that raises cache hit rates without returning a
wrong answer, while cutting latency and inference cost.

## Documentation

The root-level `ARCHITECTURE.md`, `API_REFERENCE.md`, `CONFIGURATION.md`, and
`ROADMAP.md` are symlinks into `docs/`.

| Document | What it covers |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, request lifecycle, cache pipeline, workers, tenant isolation. |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | Endpoint index, auth, headers, error behavior. |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | How configuration loads, the cache-mode contract, key knobs. |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Postgres tables, RLS policies, Qdrant collection schema. |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Setup, commands, tests, migrations, conventions. |
| [docs/limitations.md](docs/limitations.md) | Intentional boundaries and known gaps — read before proposing changes. |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Plan of record: what landed, what is open, in development order. |
| [AGENTS.md](AGENTS.md) | Contract for coding agents working in this repo. |

## Capabilities

### Multi-Provider Routing & Resiliency

Dual-protocol endpoints (`POST /api/v1/chat/completions`, `POST /api/v1/messages`
and `/v1/messages`) with retries on `408/429/5xx`, per-provider circuit breakers,
global and per-provider bulkheads, alias routing (`fast`, `cheap`, `quality`),
timeout-budget propagation (`X-Timeout-Budget-Ms`), and a typed degradation
contract exposed in `X-Gateway-Degradation`.

### Multi-Level Caching Pipeline

1. **Redis exact** — versioned SHA-256 key over every answer-affecting field.
2. **Qdrant exact** — persistent second exact layer when Redis expires.
3. **Qdrant hybrid semantic** — dense + BM25 sparse with RRF, a dense fast path
   at `QDRANT_DENSE_SEARCH_HIGH_THRESHOLD` (0.98), intent classification, and a
   cross-encoder rerank gate.

`CACHE_MODE=shadow` replays the whole pipeline out of band and records would-be
hits before you serve from it; `CACHE_MODE=active` serves. Cache writes and audit
logging are always off the response path, and any cache or inference failure
degrades to a direct provider call — never a 5xx.

### Semantic Verification & Request Intelligence

Intent classification, entity extraction, and a cache-scope hierarchy
(`GLOBAL → TENANT → USER → SESSION → CONVERSATION`); cross-encoder reranking with
a persisted per-signal `score_breakdown`; and multi-dimensional judge evaluation
(relevance, contradiction, entailment) to catch false-positive hits.

### Full Request Tracing & Latency Profiling

Per-stage timings (`redis_exact_ms`, `qdrant_exact_ms`, `qdrant_dense_ms`,
`qdrant_rrf_ms`, `intent_classify_ms`, `rerank_ms`, `provider_ms`, `total_ms`) and
a `gateway_requests` audit trail with routing decision, cache status, errors, and
evaluation verdicts.

### Quotas, Usage, & Cost Tracking

RPM/TPM quotas and prepaid budget per API key, provider, and model; prompt /
completion / cached token accounting; cost computation from provider pricing
(with upstream prompt-cache multipliers). Every request binds to a tenant and
Postgres Row-Level Security makes cross-tenant reads and writes impossible at the
storage layer.

### Prompt Token Optimization Pipeline

Opt-in pre-dispatch compaction: lossless whitespace collapse, `json-table`
tool-result tabularization, `session-dedup`, conversation context reduction
(`ccr`), headroom trimming, and lossy compression offloaded to
`inference-platform/` over HTTP. Reports savings in `X-EchoGate-Prompt-Savings`.

### Stateful Multi-Turn Conversations

`/api/v1/conversations` CRUD plus history-aware chat with atomic turn
persistence and SSE streaming parity. RLS-scoped per tenant and user.

### Observability UI and Workers

`web/` ships the chat playground, conversations, traces with a latency waterfall,
analytics (cache hit rate, cost and time saved, shadow-cache readiness), and
quota administration. Celery runs cache eviction, request evaluation, and the
shadow audit loop.

## Tech Stack

- **Gateway:** FastAPI, Pydantic v2, SQLAlchemy, Uvicorn, httpx, Poetry
- **UI:** Next.js 16, React 19, Tailwind CSS v4, Radix UI Themes
- **Storage:** Qdrant (dense + sparse vectors), Redis 7, PostgreSQL 16
- **ML:** served over HTTP by `inference-platform/` (embeddings, rerank, NLI,
  zero-shot) — no in-process models in the gateway
- **Queue:** Celery on Redis

## Quick Start

Prerequisites: Docker + Compose, Poetry, Node.js 20+, and Ollama if you want
local models.

```bash
# 1. Bring up inference, infrastructure, gateway, workers, and the UI.
#    Runs migrations and mints an admin key; add --no-ui for backend only.
./setup.sh

# 2. Mint a tenant API key with the admin key printed by setup.sh.
curl -X POST http://localhost:8000/api/v1/admin/api-keys/ \
  -H "X-Admin-Key: <YOUR_ADMIN_KEY>" -H "Content-Type: application/json" \
  -d '{"name":"local-dev","tenant_id":"acme","user_id":"dev"}'

# 3. Call it.
curl -X POST http://localhost:8000/api/v1/chat/completions \
  -H "Content-Type: application/json" -H "X-API-Key: <YOUR_API_KEY>" \
  -d '{"service_name":"ollama","model":"qwen2.5-coder:7b",
       "messages":[{"role":"user","content":"Write a prime check in Python."}]}'
```

The container-only Compose stack publishes no host ports, so those two calls
assume a host-native gateway (`./dev.sh`) or your own proxy; inside the stack, run
them through `docker compose exec echo-gate …` and use container-side URLs.
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) covers both paths, the hot-reload dev
stack (`docker-compose.dev.yml`), environment setup, and the admin-key workflow in
full.

`CACHE_MODE` defaults to `disabled`; set it to `shadow` to observe the cache
pipeline, or `active` to serve from it. See
[docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the full set of knobs.

## License

Echo Gate is licensed under the [GNU Affero General Public License v3.0](LICENSE)
or any later version.

**Self-hosting safe harbor:** running *unmodified* Echo Gate privately inside your
organization requires nothing further. If you modify it and serve it to third
parties over a network, AGPL section 13 requires you to offer those users your
modified source, or obtain a commercial license from the maintainers.

See [NOTICE](NOTICE) and [CONTRIBUTING.md](CONTRIBUTING.md) before your first pull
request.
