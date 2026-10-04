# Echo Gate LLM Gateway

**Echo Gate** is an asynchronous, high-performance LLM API gateway and proxy built with **FastAPI**. It intercepts standard OpenAI-compatible `chat/completions` requests and intelligently routes them to various LLM providers (Local Ollama, Google GenAI/Gemini, OpenAI, Groq, and OpenRouter).

Its standout feature is **Multi-level Semantic Caching**: by converting incoming prompts into vector embeddings and storing them in a vector database, Echo Gate can serve identical or nearly-identical requests instantly from the cache. It combines exact matching with Redis and semantic matching with Qdrant, supplemented by a Cross-Encoder for reranking, dynamic cache confidence thresholds, and intent classification. This drastically reduces latency, compute costs, and API usage.

Echo Gate also includes a **Next.js Frontend** (in the `ui/` directory) for managing and monitoring the gateway.

## 🚀 Key Features

- **Multi-Provider Routing:** Standardizes requests and dynamically routes them to:
  - **Ollama** (Local models like `deepseek-r1`, `qwen2.5-coder`)
  - **Google GenAI** (`gemini-1.5-pro`, `gemini-1.5-flash`)
  - **OpenAI** (`gpt-4o`, `gpt-4o-mini`)
  - **Groq** and **OpenRouter**
- **Multi-Level Caching Pipeline:**
  - **Redis:** Provides instantaneous responses for exact prompt matches.
  - **Qdrant (Semantic):** Uses local embeddings (e.g., `nomic-embed-text` or `SentenceTransformers`) to retrieve semantically similar prior requests.
- **Advanced Request Processing:**
  - **Intent Classification & Entity Extraction:** Categorizes requests and parses entities to dynamically adjust cache TTLs and caching strictness.
  - **Reranking:** Evaluates Qdrant semantic search candidates using local cross-encoders (`ms-marco-MiniLM-L-6-v2`) to ensure high relevance before returning a cache hit.
  - **LLM Quotas & Usage Tracking:** Integrates Postgres to track user tokens and apply quotas.
- **High Concurrency & Async I/O:** Built purely on Python's `asyncio` and `httpx`, ensuring the gateway never blocks.
- **Enterprise Architecture:** Structured like a robust backend using Dependency Injection, clean modules (`llm`, `embedding`, `qdrant`, `chat`, `reranking`, `redis`), Pydantic validation, and SQLAlchemy for relational data.
- **Observability:** Custom middlewares inject correlation IDs across the stack, paired with a colorful structured logger for easy debugging.

## 🏗️ Architecture Overview

The system is composed of several independent modules wired together via a Dependency Injection container:

1. **`ChatController`:** The FastAPI entry point. It accepts OpenAI-compatible JSON requests.
2. **`LlmRouterService`:** The core orchestrator.
   - Extracts the prompt and model name.
   - Validates quotas via `LlmQuotaService`.
   - Checks **Redis** for an exact match.
   - If no exact match, classifies intent and extracts entities.
   - Calls **EmbeddingService** to vectorize the prompt.
   - Queries **QdrantService** to find semantic matches.
   - If candidates are found, uses **RerankerService** and **CacheConfidenceEvaluator** to score and pick the best cached response based on the request's structural parameters (e.g., `max_tokens`, `stop`).
   - On a cache miss, routes the request to the correct provider and caches the response.
3. **Provider Services:** `LlmProviderService` handles the HTTP implementations for Ollama, OpenAI, Gemini, Groq, and OpenRouter.
4. **`EmbeddingService`:** Generates embeddings locally (via Ollama or `SentenceTransformer`).
5. **`QdrantService` / `RedisService`:** Manages the vector embeddings and exact hashes.

## 📦 Tech Stack

*   **Backend:** FastAPI / Python 3.11+ (Poetry)
*   **Frontend:** Next.js 15+ (in `ui/` directory)
*   **Databases:** Qdrant (Vector), Redis (Key-Value), SQLite/PostgreSQL (Relational via SQLAlchemy)
*   **Local Inference:** Ollama, SentenceTransformers, Cross-Encoders
*   **Background Jobs:** Celery (planned/implemented for async evaluations)

## ⚙️ Setup & Installation

### 1. Prerequisites
- [Poetry](https://python-poetry.org/) installed
- [Node.js](https://nodejs.org/en) (for the UI)
- [Ollama](https://ollama.ai/) installed and running locally on port `11434`
- Docker & Docker Compose (for running Qdrant & Redis)

### 2. Environment Variables
Copy the example environment file in the `app` directory:
```bash
cp app/.env.example app/.env
```
Fill in your desired API Keys, base URLs, and model settings. The system heavily relies on `config.py` which reads from these variables to customize models, thresholds, and timeouts without changing code.

### 3. Run the Infrastructure
Spin up the Qdrant and Redis databases, and optionally the echo-gate container itself using Docker Compose:
```bash
docker-compose up -d
```

### 4. Download Local Models
Make sure you have the required models pulled in Ollama (if using Ollama for embeddings or classification):
```bash
ollama run nomic-embed-text:latest
ollama run qwen2.5-coder:1.5b
# add any other models you configure in your .env
```

### 5. Install and Run the API (Backend)
Install the dependencies using Poetry:
```bash
poetry install
```

Run database migrations to initialize SQLite/Postgres:
```bash
poetry run alembic upgrade head
```

Start the FastAPI server:
```bash
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 6. Install and Run the UI (Frontend)
Open a new terminal and navigate to the `ui` folder:
```bash
cd ui
npm install
npm run dev
```

## 📡 Usage

Echo Gate exposes an endpoint that mirrors the OpenAI API. You can direct your existing scripts to `http://localhost:8000`.

### Example Request
By default, the `service_name` routes to `"ollama"`.

```bash
curl -X POST "http://localhost:8000/api/v1/chat/completions" \
     -H "Content-Type: application/json" \
     -d '{
       "model": "qwen2.5-coder:7b",
       "max_tokens": 100,
       "messages": [{"role": "user", "content": "Write a python script to reverse a string."}]
     }'
```

### Routing to Cloud Providers
Add the `service_name` parameter (e.g., `google-genai`, `openai`, `groq`, `openrouter`) to route out to the cloud:
```bash
curl -X POST "http://localhost:8000/api/v1/chat/completions" \
     -H "Content-Type: application/json" \
     -d '{
       "service_name": "google-genai",
       "model": "gemini-3.5-flash-lite",
       "messages": [{"role": "user", "content": "Write a python script to reverse a string."}]
     }'
```

### Testing the Cache
Send the exact same request twice to test manually. The first time will take several seconds as it hits the upstream model. The second time, Echo Gate will log a Redis or Qdrant cache hit and return the response in milliseconds!
