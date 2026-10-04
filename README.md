# Echo Gate LLM Gateway

**Echo Gate** is an asynchronous, high-performance LLM API gateway and proxy built with **FastAPI**. It intercepts standard OpenAI-compatible `chat/completions` requests and intelligently routes them to various LLM providers (Local Ollama, Google GenAI/Gemini, and OpenAI). 

Its standout feature is **Multi-level Semantic Caching**: by converting incoming prompts into vector embeddings and storing them in a vector database, Echo Gate can serve identical or nearly-identical requests instantly from the cache. It combines exact matching with Redis and semantic matching with Qdrant, supplemented by a Cross-Encoder for reranking, drastically reducing latency, compute costs, and API usage.

## 🚀 Key Features

- **Multi-Provider Routing:** Standardizes requests and dynamically routes them to:
  - **Ollama** (Local models like `deepseek-r1`, `qwen2.5-coder`)
  - **Google GenAI** (`gemini-1.5-pro`, `gemini-1.5-flash`)
  - **OpenAI** (`gpt-4o`, `gpt-4o-mini`)
- **Multi-Level Caching Pipeline:** 
  - **Redis:** Provides instantaneous responses for exact prompt matches.
  - **Qdrant (Semantic):** Uses local embeddings (e.g., `nomic-embed-text` or `SentenceTransformers`) to retrieve semantically similar prior requests.
- **Advanced Reranking:**
  - Evaluates Qdrant semantic search candidates using a local cross-encoder (`ms-marco-MiniLM-L-6-v2`) to ensure high relevance before returning a cache hit.
- **High Concurrency & Async I/O:** Built purely on Python's `asyncio` and `httpx`, ensuring the gateway never blocks while waiting for slow LLM generations.
- **Enterprise Architecture:** Structured like a robust backend using Dependency Injection, clean modules (`llm`, `embedding`, `qdrant`, `chat`, `reranking`, `redis`), Pydantic validation, and SQLAlchemy for relational data.
- **Observability:** Custom middlewares inject correlation IDs across the stack, paired with a colorful structured logger for easy debugging.

## 🏗️ Architecture Overview

The system is composed of several independent modules wired together via a Dependency Injection container:

1. **`ChatController`:** The FastAPI entry point. It accepts OpenAI-compatible JSON requests.
2. **`LlmService`:** The core orchestrator.
   - Extracts the prompt and model name.
   - Checks **Redis** for an exact match.
   - If no exact match, calls **EmbeddingService** to vectorize the prompt.
   - Queries **QdrantService** to find semantic matches.
   - If candidates are found, uses **RerankerService** to score and pick the best cached response.
   - On a total cache miss, routes the request to the correct provider (`Ollama`, `Gemini`, or `OpenAI`) and updates caches.
3. **`EmbeddingService`:** Generates embeddings locally (via Ollama or `SentenceTransformer`).
4. **`QdrantService`:** Manages the `prompt_embeddings` collection in Qdrant for fast cosine similarity searches.
5. **`RerankerService`:** Validates and scores retrieved cache candidates.
6. **`RedisService`:** Handles fast caching for exact request hashing.

## 📦 Tech Stack

*   **Framework:** FastAPI / Python 3.11+
*   **Package Manager:** Poetry
*   **Databases:** Qdrant (Vector), Redis (Key-Value), SQLite/PostgreSQL (Relational via SQLAlchemy)
*   **Local Inference:** Ollama, SentenceTransformers, Cross-Encoders
*   **HTTP Client:** httpx

## ⚙️ Setup & Installation

### 1. Prerequisites
- [Poetry](https://python-poetry.org/) installed
- [Ollama](https://ollama.ai/) installed and running locally on port `11434`
- Docker & Docker Compose (for running Qdrant & Redis)

### 2. Download Local Models
Make sure you have the required models pulled in Ollama:
```bash
ollama run nomic-embed-text:latest
ollama run qwen2.5-coder:1.5b
# add any other models you plan to use
```

### 3. Environment Variables
Create a `.env` file in the root directory:
```ini
# API Keys for external services
GEMINI_API_KEY=your_google_genai_key
OPENAI_API_KEY=your_openai_key

# Local infrastructure endpoints (defaults)
OLLAMA_URL=http://localhost:11434
QDRANT_URL=http://localhost:6333
REDIS_URL=redis://localhost:6379
DATABASE_URL=sqlite:///./echo_gate.db
```

### 4. Run the Infrastructure
Spin up the Qdrant and Redis databases using Docker Compose:
```bash
docker-compose up -d
```

### 5. Install and Run Echo Gate
Install the dependencies using Poetry:
```bash
poetry install
```

Start the FastAPI server:
```bash
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 📡 Usage

Echo Gate exposes an endpoint that mirrors the OpenAI API. You can direct your existing scripts to `http://localhost:8000`.

### Example Request
By default, the `service_name` routes to `"ollama"`.

```bash
curl -X POST "http://localhost:8000/api/v1/chat/completions" \
     -H "Content-Type: application/json" \
     -d '{
       "model": "deepseek-r1:14b",
       "messages": [{"role": "user", "content": "Write a python script to reverse a string."}]
     }'
```

### Routing to Gemini
Add the `service_name` parameter to route out to the cloud:
```bash
curl -X POST "http://localhost:8000/api/v1/chat/completions" \
     -H "Content-Type: application/json" \
     -d '{
       "service_name": "google-genai",
       "model": "gemini-1.5-pro",
       "messages": [{"role": "user", "content": "Write a python script to reverse a string."}]
     }'
```

### Testing the Cache
The repository now includes dedicated testing scripts in `app/scripts/`:
- **`seed_tests.py`**: Seeds the vector cache with numerous requests.
- **`run_tests.py`**: Evaluates cache hit rates and latencies.

Send the exact same request twice to test manually. The first time takes several seconds. The second time, Echo Gate will log a Redis or Qdrant cache hit and return the response in milliseconds!
