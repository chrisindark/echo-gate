# Echo Gate LLM Gateway

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)

**Echo Gate** is an asynchronous, high-performance LLM API gateway, proxy, and observability platform built with **FastAPI** (Python 3.11+) and accompanied by an independent **Next.js 16** frontend in `web/`.

Echo Gate accepts OpenAI-compatible `chat/completions` requests and routes them dynamically across local and cloud providers (**Ollama**, **Google GenAI / Gemini**, **OpenAI**, **Groq**, and **OpenRouter**). Its core capability is an enterprise-grade **Multi-Level Semantic Caching & Verification Pipeline** combining Redis, Qdrant hybrid search, cross-encoder reranking, intent classification, and asynchronous judge evaluations to maximize cache hit rates, eliminate hallucinations, protect user privacy, and drastically cut latency and inference costs.

## 📚 Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, request lifecycle, cache pipeline, background workers, multi-tenant RLS.
- [docs/API_REFERENCE.md](docs/API_REFERENCE.md) — every endpoint, request/response shape, headers, errors.
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — every environment variable and its default.
- [docs/DATA_MODEL.md](docs/DATA_MODEL.md) — Postgres tables, RLS policies, Qdrant collection schema.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — setup, commands, tests, migrations, conventions.
- [docs/ROADMAP.md](docs/ROADMAP.md) — plan of record: landed features and planned work in development order.
- [AGENTS.md](AGENTS.md) — concise guide for coding agents working in this repo.

---

## 🚀 Key Features

### 1. Multi-Provider Routing & Resiliency

- Standardized OpenAI chat completion endpoint routing to:
  - **Ollama**: Local models (`qwen3:8b`, `qwen2.5-coder:{1.5b,3b,7b,14b}`)
  - **Google GenAI**: Gemini models (`gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`, `gemini-3.8-flash`)
  - **OpenAI**: `gpt-4o-mini`, etc.
  - **Groq**: High-throughput open-weights (`openai/gpt-oss-20b`, `openai/gpt-oss-safeguard-20b`)
  - **OpenRouter**: Free and commercial community models (`qwen/qwen3.8-27b:free`, `google/gemma-4-31b-it:free`)
- **Automated Fallback & Exponential Retries**: Configurable automatic fallback provider and model switching with retry policies to prevent client-facing outages.

### 2. Multi-Level Caching Pipeline

- **Layer 1: Redis Exact Match**:
  - Sub-millisecond retrieval for identical requests using versioned SHA-256 prompt and configuration hashes.
  - Granular TTL management with configurable bypass flags (`USE_REDIS_EXACT_MATCHING`).
- **Layer 2: Qdrant Exact Hash Match**:
  - Serves as a persistent secondary exact cache if Redis keys expire or are evicted.
- **Layer 3: Qdrant Hybrid Semantic Search**:
  - **Dual-Vector Architecture**: Indexes both dense vectors (`prompt_embedding` via `sentence-transformers`, default `all-MiniLM-L6-v2`) and sparse lexical tokens (BM25) for robust semantic and keyword matching.
  - **Reciprocal Rank Fusion (RRF)**: Merges dense and sparse vector queries over system and user prompt segments.
  - **Fast-Path Dense Bypass**: Bypasses expensive reranking and intent pipelines when dense cosine similarity exceeds the high-confidence threshold (`QDRANT_DENSE_SEARCH_HIGH_THRESHOLD`, default `0.98`).
  - **Asynchronous Background Persistence**: Responses are returned to the client immediately on cache misses; cache vectorization and database insertion run out-of-band via background tasks.
  - **Shadow Cache Pipeline (`CACHE_MODE=shadow`)**: Serve every request from the provider while replaying the full cache pipeline in the background — recording would-be hits, match types, judge equivalence, and projected latency savings, and warming the cache corpus before you flip to `active`.
  - **Toggleable Infrastructure**: Ability to independently disable Redis or Qdrant search layers via environment configuration.

### 3. Semantic Verification & Request Intelligence

- **Intent Classification & Analysis**:
  - Classifies query intent (e.g. `general_query`, `code_generation`, `code_explanation`, `debugging`, `summarization`, `chitchat`, `weather`, `finance_market`, `factual_static`, …).
  - Extracts extended semantic attributes: `time_sensitivity`, `core_operation`, `core_subject`, `subject_modifier`, and `action_modifier`.
  - Dynamically calculates cache TTLs and confidence thresholds based on query time-sensitivity and domain volatility.
- **Entity Extraction & Cache Scope Hierarchy**:
  - Named entity and keyword extraction determine privacy boundaries.
  - Enforces strict cache isolation levels: `GLOBAL` $\rightarrow$ `TENANT` $\rightarrow$ `USER` $\rightarrow$ `SESSION` $\rightarrow$ `CONVERSATION`.
- **Cross-Encoder Reranking (`RerankerService`)**:
  - Scores candidate semantic matches with a `BAAI/bge-reranker-base` cross-encoder (`RERANKER_MODEL_NAME`), comparing request and candidate user segments.
  - Combines the base score with bounded signal factors (model, service, intent, extracted metadata, entities, system prompt, temperature, instruction constraints) via `RERANKER_SCORING_MODE` (`multiplicative` by default, or `additive`).
  - Persists the top candidate's `score_breakdown` (scoring mode, base/final scores, per-signal factors, details) on the `gateway_requests` trace for calibration.
- **Multi-Dimensional Judge Evaluation (`JudgeService`)**:
  - Evaluates cache quality across **Relevance (1-5)**, **Contradiction (0/1)**, and **Entailment (1-5)**.
  - Employs local DeBERTa-v3 NLI cross-encoders alongside optional LLM judges (Gemini, OpenAI, Groq) to identify false positive cache hits.

### 4. Full Request Tracing & Latency Profiling

- **Microsecond Latency Breakdown**: Every request records fine-grained timestamps across every pipeline stage:
  - `redis_exact_ms`
  - `qdrant_exact_ms`
  - `qdrant_dense_ms`
  - `qdrant_rrf_ms`
  - `intent_classify_ms`
  - `rerank_ms`
  - `provider_ms`
  - `total_ms`
- **Gateway Requests Log**: Comprehensive audit trail in PostgreSQL storing prompts, responses, routing decisions (`REDIS_EXACT`, `QDRANT_EXACT`, `QDRANT_SEMANTIC`, `QDRANT_FAST`, `LLM`), latencies, errors, and evaluation statuses.

### 5. Quotas, Usage, & Cost Tracking

- **Token Usage Accounting**: Tracks prompt, completion, total, cache-read, and cache-creation tokens per request.
- **Cost Calculation**: Automated dollar cost computation based on provider and model token pricing tiers.
- **Rate & Token Limits**: Enforces Tokens-Per-Minute (TPM) quota rules per API key, provider, and model.
- **Multi-Tenant Isolation (Postgres RLS)**: Every request resolves an API key to a tenant/user and the gateway binds that identity to the database transaction. Row-Level Security policies on the traces, usage, quota, and conversation tables make cross-tenant reads and writes impossible at the storage layer.

### 6. Observability UI (Next.js 16)

- Independent frontend located in `web/` (Next.js 16, React 19, Tailwind CSS, Radix UI Themes):
  - **Chat Playground (`/`)**: Interactive chat interface with real-time streaming, cache hit indicators, provider and model selectors, and configuration toggles.
  - **Traces Dashboard (`/traces`)**: Real-time table of all gateway traffic with routing decisions, cache statuses, and latencies.
  - **Trace Detail View (`/traces/[id]`)**: Interactive waterfall visualization displaying the exact lifecycle latency of each pipeline stage, extracted intent metadata, and judge scores.
  - **Analytics Dashboard (`/analytics`)**: Visual summaries of KPIs, cache hit rates, cost and latency saved, model breakdowns, and time-series trends, plus a cache-readiness indicator backed by shadow-mode telemetry.
  - **Quota Management (`/quota`)**: Scoped admin interface for Tokens-Per-Minute (TPM) and budget rules.

| Chat Playground | Traces & Latency Waterfall |
| --- | --- |
| ![Chat Playground](docs/assets/screenshots/chat-playground.png) | ![Traces Dashboard](docs/assets/screenshots/traces-dashboard.png) |

| Trace Detail & Waterfall | Analytics Dashboard |
| --- | --- |
| ![Trace Detail](docs/assets/screenshots/trace-detail.png) | ![Analytics Dashboard](docs/assets/screenshots/analytics-dashboard.png) |

### 7. Background Worker (Celery)

- **Cache Eviction**: Periodically purges expired vector points from Qdrant (`delete_expired_vectors`).
- **Asynchronous Evaluation Pipeline**: Background worker (`evaluate_pending_requests`) evaluates pending request logs with cross-encoders and NLI models to identify false-positive cache hits without degrading hot-path serving latency.

---

## 🏗️ Architecture & Request Lifecycle

```text
                        ┌────────────────────────────────────────────────┐
                        │      Client Request (OpenAI Compatible)        │
                        └───────────────────────┬────────────────────────┘
                                                │
                                       [Auth / Quota Guard]
                                                │
                                                ▼
                                    ┌───────────────────────┐
                                    │   Redis Exact Cache   │──(Hit)──► Return Cached JSON
                                    └───────────┬───────────┘
                                                │ (Miss)
                                                ▼
                                    ┌───────────────────────┐
                                    │  Qdrant Exact Cache   │──(Hit)──► Return & Warm Redis
                                    └───────────┬───────────┘
                                                │ (Miss)
                                                ▼
                                    ┌───────────────────────┐
                                    │  Dense Hybrid Search  │
                                    │    (Qdrant Fast Path) │──(Score >= 0.98)──► Return Cached JSON
                                    └───────────┬───────────┘
                                                │ (< 0.98)
                                                ▼
                       ┌─────────────────────────────────────────────────┐
                       │ Hybrid Search (Dense + Sparse BM25 RRF Candidates│
                       └────────────────────────┬────────────────────────┘
                                                │
                                    ┌───────────┴───────────┐
                                    │ Intent Classification │
                                    │   & Entity Extraction │
                                    └───────────┬───────────┘
                                                │
                                    ┌───────────┴───────────┐
                                    │ Cross-Encoder Rerank  │
                                    │ & Confidence Decision │
                                    └───────────┬───────────┘
                                                │
                       ┌────────────────────────┴────────────────────────┐
                       │                                                 │
                  (Cache Hit)                                       (Cache Miss)
                       │                                                 │
                       ▼                                                 ▼
            Return Cached Response                          Call Upstream LLM Provider
                                                            (Ollama / Gemini / OpenAI / Groq)
                                                                         │
                                                                         ▼
                                                            Background Cache Write (Async)
                                                            (Redis + Qdrant Dual Ingestion)
                                                                         │
                                                                         ▼
                                                            Asynchronous Request Audit & Trace
                                                            (Logged to PostgreSQL for Celery)
```

---

## 📦 Tech Stack

- **Backend Gateway**: FastAPI, Python 3.11+, Pydantic v2, SQLAlchemy, Uvicorn, httpx, Poetry
- **Frontend Dashboard**: Next.js 16, React 19, Tailwind CSS v4, Radix UI Themes, Lucide Icons
- **Vector & Cache Storage**: Qdrant (dense + sparse vectors), Redis 7 (in-memory key-value)
- **Relational Storage**: PostgreSQL 16 (request logs, token usage, quotas, evaluations)
- **Machine Learning & Inference**: Ollama, `sentence-transformers`, `CrossEncoder`, DeBERTa-v3 NLI, Google GenAI SDK
- **Task Queue**: Celery (backed by Redis)

---

## ⚙️ Setup & Installation

### 1. Prerequisites

- [Poetry](https://python-poetry.org/) (Python dependency management)
- [Node.js 20+](https://nodejs.org/) (for the Next.js UI)
- [Docker & Docker Compose](https://www.docker.com/)
- [Ollama](https://ollama.ai/) installed and running locally on port `11434` (if using local models)

### 2. Prepare Local Models (Ollama)

The gateway enforces a per-provider model allowlist
(`ALLOWED_MODELS` in `app/modules/chat/chat_schema.py`). For Ollama that list is
`qwen3:8b` and `qwen2.5-coder:{1.5b,3b,7b,14b}`. Pull the ones you need:

```bash
ollama pull qwen3:8b
ollama pull qwen2.5-coder:1.5b
ollama pull qwen2.5-coder:3b
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5-coder:14b
```

Embeddings are computed locally with `sentence-transformers`
(`EMBEDDING_MODEL_NAME`, default `all-MiniLM-L6-v2`), not via Ollama.

### 3. Environment Configuration

Create your environment file in `api/app/`:

```bash
cp api/app/.env.example api/app/.env.local
```

Configure your keys and endpoints:

```ini
PYTHON_ENV=local

# Provider API Keys
GEMINI_API_KEY=your_gemini_key
OPENAI_API_KEY=your_openai_key
GROQ_API_KEY=your_groq_key
OPENROUTER_API_KEY=your_openrouter_key

# Database Connections
# The app connects as a non-owner role so Postgres RLS applies.
DATABASE_URL=postgresql+psycopg://echo_gate_app:echo_gate_app@localhost:5432/echo_gate
DATABASE_URL_READ=postgresql+psycopg://echo_gate_app:echo_gate_app@localhost:5432/echo_gate
# Owner credentials, used only by Alembic (RLS does not apply to table owners).
MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/echo_gate
DB_APP_ROLE=echo_gate_app
DB_APP_PASSWORD=echo_gate_app
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
QDRANT_URL=http://localhost:6333

# Ollama Endpoint
OLLAMA_URL=http://localhost:11434

# Caching Configuration
CACHE_MODE=active
USE_REDIS_EXACT_MATCHING=true
USE_QDRANT_EXACT_MATCHING=true
USE_QDRANT_SEMANTIC_MATCHING=true
DEFAULT_LLM_SERVICE=ollama
```

> `CACHE_MODE` defaults to `disabled`; set it to `shadow` to observe the cache
> pipeline without serving from it, or `active` to serve from it. When it is
> unset, the `USE_*_MATCHING` flags decide (`active` if any is `"true"`), and
> those flags default to `"false"`. `DEFAULT_LLM_SERVICE` otherwise defaults to
> `google-genai`. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the full
> list, including the semantic-search thresholds and model settings.

### 4. Start Infrastructure Containers

Start PostgreSQL, Redis, and Qdrant:

```bash
docker compose -f deploy/compose/docker-compose.yml up -d postgres redis qdrant
```

### 5. Install Dependencies & Run Database Migrations

```bash
# Install backend dependencies
poetry install

# Run database migrations (as the owner role, via MIGRATION_DATABASE_URL)
# This also creates the non-owner application role and the RLS policies.
poetry run alembic upgrade head
```

The baseline migration provisions a non-superuser `echo_gate_app` role and enables
Row-Level Security on every tenant-scoped table. The gateway connects as that role,
so queries are automatically restricted to the tenant resolved from the request's
`X-API-Key`. Trusted background jobs (Celery) run in system mode and can operate
across tenants.

### 6. Create an API Key

Every request's tenant context comes from an API key.

First, mint an admin key using the CLI tool:

```bash
poetry run python -m app.scripts.create_admin_key create --name "local-admin" --scopes "admin:*"
```

Store the displayed admin key (`ega_...`). Then create a tenant API key:

```bash
curl -X POST http://localhost:8000/api/v1/admin/api-keys/ \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: <YOUR_ADMIN_KEY>" \
  -d '{"name": "local-dev", "tenant_id": "acme", "user_id": "dev"}'
```

The response includes the plaintext `api_key` (shown once). Send it as
`X-API-Key` on subsequent requests; the gateway maps it to the tenant/user and
sets the Postgres RLS context. When no key is supplied the request is treated as
the `anonymous` tenant.

### 7. Start the Services

**API Gateway:**

```bash
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Celery Background Worker:**

```bash
poetry run celery -A app.worker.celery_app worker --loglevel=info
```

**Celery Beat (Optional - for scheduled tasks):**

```bash
poetry run celery -A app.worker.celery_app beat --loglevel=info
```

**Frontend UI:**

```bash
cd web
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) to view the gateway dashboard, traces, and analytics.

---

## 📡 API Usage & Examples

Echo Gate exposes standard endpoints under `/api/v1`.

### 1. Standard Chat Completion (Ollama)

```bash
curl -X POST "http://localhost:8000/api/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "ollama",
    "model": "qwen2.5-coder:7b",
    "messages": [
      {"role": "user", "content": "Write a python function to check if a number is prime."}
    ],
    "temperature": 0.7
  }'
```

### 2. Cloud Provider Routing (Google Gemini / OpenAI / Groq)

```bash
curl -X POST "http://localhost:8000/api/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "google-genai",
    "model": "gemini-3.5-flash-lite",
    "messages": [
      {"role": "user", "content": "Explain quantum computing in one simple paragraph."}
    ]
  }'
```

### 3. Inspecting Semantic Cache Directly

Test candidate retrieval without hitting an LLM provider:

```bash
curl -X POST "http://localhost:8000/api/v1/chat/completions/semantic-search" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5-coder:7b",
    "messages": [
      {"role": "user", "content": "How do I check for prime numbers in Python?"}
    ]
  }'
```

### 4. Fetching Request Traces & Analytics

- **List Traces**: `GET /api/v1/gateway-requests/?skip=0&limit=20`
- **Trace Details with Latency Breakdown**: `GET /api/v1/gateway-requests/{log_id}`
- **Trace by Exact Hash**: `GET /api/v1/gateway-requests/by-hash/{exact_hash}`
- **Analytics Dashboard** (KPIs + cache performance + model breakdown + latency):
  `GET /api/v1/analytics/dashboard`
- **Latency Metrics Detail**: `GET /api/v1/analytics/latency`
- **Shadow Cache Readiness** (would-be hit rate, equivalence, projected savings):
  `GET /api/v1/shadow-cache/summary`
- **Shadow Evaluations Drill-Down**: `GET /api/v1/shadow-cache/evaluations`

### 5. Other Endpoints

| Method             | Path                                           | Purpose                                                     |
| ------------------ | ---------------------------------------------- | ----------------------------------------------------------- |
| `POST`             | `/api/v1/chat/completions/exact-search`        | Test the exact-cache lookup only.                           |
| `POST`             | `/api/v1/chat/completions/cache-save`          | Write a `{request, response}` pair into the cache directly. |
| `POST`             | `/api/v1/qdrant/search`                        | Dense vector search against the cache collection.           |
| `POST`             | `/api/v1/qdrant/search/rrf`                    | Hybrid dense + sparse RRF cache search.                     |
| `POST`             | `/api/v1/verifier/predict/rerank`              | Cross-encoder rerank of candidate strings.                  |
| `GET`/`POST`       | `/api/v1/llm-usage/`                           | List / create token-usage logs.                             |
| `POST`             | `/api/v1/llm-usage/calculate-costs`            | Queue background cost calculation.                          |
| `GET`/`POST`/`DEL` | `/api/v1/admin/api-keys/`                      | Create, list, and revoke API keys.                          |
| `GET`/`POST`       | `/api/v1/conversations`                        | Create / list conversations (RLS-scoped).                   |
| `GET`              | `/api/v1/conversations/{conversation_id}`      | A conversation with its messages.                           |
| `POST`             | `/api/v1/conversations/{conversation_id}/chat` | Chat with full history; persists user + assistant messages. |
| `GET`              | `/api/v1/health`                               | Basic health; see `/live`, `/ready`, `/worker`, `/celery`.  |

The full, authoritative endpoint reference is in
[docs/API_REFERENCE.md](docs/API_REFERENCE.md); the API contract is also snapshotted
in `tests/snapshots/openapi.json`.

---

## 🛠️ Utility Scripts

The project includes helpful CLI scripts under `api/app/scripts/` (runnable as `python -m app.scripts.<name>`):

- **Run Evaluation Test Suite**: `poetry run python -m app.scripts.run_tests`
- **Seed Benchmark Prompts**: `poetry run python -m app.scripts.seed_prompts`
- **Migrate Qdrant Embeddings**: `poetry run python -m app.scripts.migrate_embeddings`
- **Manage Qdrant Collections**: `poetry run python -m app.scripts.create_qdrant`, `poetry run python -m app.scripts.check_qdrant`, and `poetry run python -m app.scripts.wipe_qdrant`

---

## 📜 License

Echo Gate is free software: licensed under the
[GNU Affero General Public License v3.0](LICENSE) or any later version.

**Self-hosting safe harbor:** AGPL's source-provision duty (section 13) is
triggered by modifying Echo Gate and making it available to users over a
network. Running _unmodified_ Echo Gate privately inside your own
organization — self-hosting for internal use — is fine and requires nothing
further. If you modify Echo Gate and serve it to third parties over a
network, you must make your modified version's source available to those
users under AGPL, or obtain a separate commercial license from the project
maintainers.

See [NOTICE](NOTICE) for the full license statement and third-party
dependency note. Contributions are licensed under AGPL-3.0 as well
(AGPL-3.0-only for the `contribution` in licensing terms); see
[CONTRIBUTING.md](CONTRIBUTING.md) before your first pull request.
