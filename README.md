# Echo Gate LLM Gateway

**Echo Gate** is an asynchronous, high-performance LLM API gateway and proxy built with **FastAPI**. It intercepts standard OpenAI-compatible `chat/completions` requests and intelligently routes them to various LLM providers (Local Ollama, Google GenAI/Gemini, and OpenAI). 

Its standout feature is **Semantic Caching**: by converting incoming prompts into vector embeddings and storing them in a vector database, Echo Gate can serve identical or nearly-identical requests instantly from the cache, drastically reducing latency, compute costs, and API usage.

## 🚀 Key Features

- **Multi-Provider Routing:** Standardizes requests and dynamically routes them to:
  - **Ollama** (Local models like `deepseek-r1`, `qwen2.5-coder`)
  - **Google GenAI** (`gemini-1.5-pro`, `gemini-1.5-flash`)
  - **OpenAI** (`gpt-4o`, `gpt-4o-mini`)
- **Semantic Caching:** 
  - Uses local `nomic-embed-text` embeddings (via Ollama) to vectorize incoming prompts.
  - Queries a local **Qdrant** vector database to find semantically matching prior requests (99% similarity threshold).
  - On a cache hit, returns the response instantly.
  - On a cache miss, generates the response and asynchronously saves it to Qdrant for future use.
- **High Concurrency & Async I/O:** Built purely on Python's `asyncio` and `httpx`, ensuring the gateway never blocks while waiting for slow LLM generations.
- **Enterprise Architecture:** Structured like a robust backend using Dependency Injection, clean modules (`llm`, `embedding`, `qdrant`, `chat`), and Pydantic validation.

## 🏗️ Architecture Overview

The system is composed of several independent modules wired together via a Dependency Injection container:

1. **`ChatController`:** The FastAPI entry point. It accepts OpenAI-compatible JSON requests.
2. **`LlmService`:** The core orchestrator.
   - Extracts the prompt and model name.
   - Calls `EmbeddingService` to get the prompt's vector.
   - Calls `QdrantService` to check for a cache hit.
   - Routes the request to the correct provider (`_generate_ollama_completion`, `_generate_gemini_completion`, or `_generate_openai_completion`).
3. **`EmbeddingService`:** Communicates with your local Ollama instance to generate 768-dimensional vectors using `nomic-embed-text:latest`.
4. **`QdrantService`:** Manages the `llm_cache` collection in Qdrant. Handles cosine similarity searches and async upserts of new request/response pairs.

## 📦 Tech Stack

*   **Framework:** FastAPI / Python 3.11
*   **Package Manager:** Poetry
*   **Vector Database:** Qdrant
*   **Local Inference:** Ollama
*   **HTTP Client:** httpx

## ⚙️ Setup & Installation

### 1. Prerequisites
- [Poetry](https://python-poetry.org/) installed
- [Ollama](https://ollama.ai/) installed and running locally on port `11434`
- Docker & Docker Compose (for running Qdrant)

### 2. Download Local Models
Make sure you have the required models pulled in Ollama:
```bash
ollama run nomic-embed-text:latest
ollama run deepseek-r1:14b
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
```

### 4. Run the Infrastructure
Spin up the Qdrant vector database using Docker Compose (assuming you have a `docker-compose.yml` for it):
```bash
docker-compose up -d qdrant
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

Echo Gate exposes an endpoint that mirrors the OpenAI API. You can direct your existing LangChain, LlamaIndex, or custom scripts to `http://localhost:8000`.

### Example Request
By default, the `service_name` routes to `"ollama"`.

```bash
curl -X POST "http://localhost:8000/api/chat/completions" \
     -H "Content-Type: application/json" \
     -d '{
       "model": "deepseek-r1:14b",
       "messages": [{"role": "user", "content": "Write a python script to reverse a string."}]
     }'
```

### Routing to Gemini
Simply add the `service_name` parameter to route out to the cloud:
```bash
curl -X POST "http://localhost:8000/api/chat/completions" \
     -H "Content-Type: application/json" \
     -d '{
       "service_name": "google-genai",
       "model": "gemini-1.5-pro",
       "messages": [{"role": "user", "content": "Write a python script to reverse a string."}]
     }'
```

### Testing the Cache
Send the exact same request twice. The first time, it will take several seconds as the model generates the response. The second time, Echo Gate will log `Serving response from semantic cache.` and return the response in milliseconds!
