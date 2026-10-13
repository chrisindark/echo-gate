# Echo Gate LLM Gateway

**Echo Gate** is an asynchronous, high-performance LLM API gateway, proxy, and observability platform built with **FastAPI** (Python 3.11+) and accompanied by an independent **Next.js 16** frontend in `ui/`.

Echo Gate accepts OpenAI-compatible `chat/completions` requests and routes them dynamically across local and cloud providers (**Ollama**, **Google GenAI / Gemini**, **OpenAI**, **Groq**, and **OpenRouter**). Its core capability is an enterprise-grade **Multi-Level Semantic Caching & Verification Pipeline** combining Redis, Qdrant hybrid search, cross-encoder reranking, intent classification, and asynchronous judge evaluations to maximize cache hit rates, eliminate hallucinations, protect user privacy, and drastically cut latency and inference costs.

---

## 🚀 Key Features

### 1. Multi-Provider Routing & Resiliency
- Standardized OpenAI chat completion endpoint routing to:
  - **Ollama**: Local models (`qwen2.5-coder`, `qwen3`, `deepseek-r1`, etc.)
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
  - **Dual-Vector Architecture**: Indexes both dense vectors (`prompt_embedding` via `nomic-embed-text` or `SentenceTransformers`) and sparse lexical tokens (BM25/SPLADE) for robust semantic and keyword matching.
  - **Reciprocal Rank Fusion (RRF)**: Merges dense and sparse vector queries over system and user prompt segments.
  - **Fast-Path Dense Bypass**: Bypasses expensive reranking and intent pipelines when dense cosine similarity exceeds high confidence thresholds (e.g., $\ge 0.99$).
  - **Asynchronous Background Persistence**: Responses are returned to the client immediately on cache misses; cache vectorization and database insertion run out-of-band via background tasks.
  - **Toggleable Infrastructure**: Ability to independently disable Redis or Qdrant search layers via environment configuration.

### 3. Semantic Verification & Request Intelligence
- **Intent Classification & Analysis**:
  - Classifies query intent (`GENERAL_QUERY`, `CODE_GENERATION`, `CREATIVE_WRITING`, `MATH_REASONING`, `FACTUAL_SEARCH`, etc.).
  - Extracts extended semantic attributes: `time_sensitivity`, `core_operation`, `core_subject`, `subject_modifier`, and `action_modifier`.
  - Dynamically calculates cache TTLs and confidence thresholds based on query time-sensitivity and domain volatility.
- **Entity Extraction & Cache Scope Hierarchy**:
  - Named entity and keyword extraction determine privacy boundaries.
  - Enforces strict cache isolation levels: `GLOBAL` $\rightarrow$ `TENANT` $\rightarrow$ `USER` $\rightarrow$ `SESSION` $\rightarrow$ `CONVERSATION`.
- **Cross-Encoder Reranking (`RerankerService`)**:
  - Scores candidate semantic matches using `ms-marco-MiniLM-L-6-v2` cross-encoders.
  - Enforces strict parameter matching (temperature compatibility, stop tokens, formatting constraints).
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

### 6. Observability UI (Next.js 16)
- Independent frontend located in `ui/` (Next.js 16, React 19, Tailwind CSS, Radix UI Themes):
  - **Traces Dashboard (`/traces`)**: Real-time table of all gateway traffic with routing decisions, cache statuses, and latencies.
  - **Trace Detail View (`/traces/[id]`)**: Interactive waterfall visualization displaying the exact lifecycle latency of each pipeline stage, extracted intent metadata, and judge scores.
  - **Analytics Dashboard (`/analytics`)**: Visual summaries of KPIs, cache hit rates, cost and latency saved, model breakdowns, and time-series trends.

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
                                    │    (Qdrant Fast Path) │──(Score >= 0.99)──► Return Cached JSON
                                    └───────────┬───────────┘
                                                │ (< 0.99)
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
Ensure required models are installed locally:
```bash
ollama pull nomic-embed-text:latest
ollama pull qwen2.5-coder:1.5b
ollama pull qwen2.5-coder:7b
ollama pull deepseek-r1:14b
```

### 3. Environment Configuration
Create your environment file in `app/`:
```bash
cp app/.env.example app/.env.local
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
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/echo_gate
DATABASE_URL_READ=postgresql://postgres:postgres@localhost:5432/echo_gate
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
QDRANT_URL=http://localhost:6333

# Ollama Endpoint
OLLAMA_URL=http://localhost:11434

# Caching Configuration
USE_REDIS_EXACT_MATCHING=true
USE_QDRANT_EXACT_MATCHING=true
USE_QDRANT_SEMANTIC_MATCHING=true
DEFAULT_LLM_SERVICE=ollama
```

### 4. Start Infrastructure Containers
Start PostgreSQL, Redis, and Qdrant:
```bash
docker-compose up -d postgres redis qdrant
```

### 5. Install Dependencies & Run Database Migrations
```bash
# Install backend dependencies
poetry install

# Run database migrations
poetry run alembic upgrade head
```

### 6. Start the Services

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
cd ui
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
- **List Traces**: `GET /api/v1/gateway-requests/logs?page=1&page_size=20`
- **Trace Details with Latency Breakdown**: `GET /api/v1/gateway-requests/logs/{id}`
- **KPI Summary**: `GET /api/v1/analytics/kpis`
- **Cache Performance Breakdown**: `GET /api/v1/analytics/cache-performance`

---

## 🛠️ Utility Scripts

The project includes helpful CLI scripts under `app/scripts/`:
- **Run Evaluation Test Suite**: `poetry run python app/scripts/run_tests.py`
- **Seed Benchmark Prompts**: `poetry run python app/scripts/seed_prompts.py`
- **Migrate Qdrant Embeddings**: `poetry run python app/scripts/migrate_embeddings.py`
- **Manage Qdrant Collections**: `poetry run python create_qdrant.py` and `poetry run python check_qdrant.py`
