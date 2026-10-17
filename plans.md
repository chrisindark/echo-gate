# Echo Gate Implementation Plans

This document tracks the approved, high-ROI architectural implementations for Echo Gate. Speculative, overly complex, or empirically disproven mechanisms (such as per-entry Bayesian logistic regression loops and live online threshold mutations) have been removed in favor of robust, decoupled designs.

---


## 2. Full Streaming Support (`stream: true`)
- **Provider Streaming**: Implement streaming methods for each provider (Ollama, Gemini, OpenAI) to yield raw Server-Sent Events (SSE) chunks.
- **Cache Hit on Stream**: When a request requests `stream: true` but hits the cache (from Redis or Qdrant), simulate a chunked stream so client parsers receive expected SSE events without breaking.
- **Cache Miss on Stream**:
  - Stream chunks back to the client immediately with minimal latency.
  - Accumulate the full generated text in memory during the stream.
  - Once the stream successfully terminates (`finish_reason == "stop"`), dispatch an asynchronous background task (`asyncio.create_task`) to write the completed completion to Redis and Qdrant.
- **Failure Safety**: If a stream drops or encounters an error mid-flight, immediately abort the cache write to prevent partial/truncated responses from poisoning the cache.

---

## 3. High-Performance Model Serving Architecture
- **Strip Heavy In-Process ML Dependencies**: Remove `sentence-transformers`, `transformers`, and `torch` from the core gateway container dependencies, reducing Docker image size from ~5GB+ to <300MB for faster startup and horizontal autoscaling.
- **Dedicated Inference Server**: Offload embeddings and cross-encoder reranking to a dedicated model server (e.g. Text Embeddings Inference / TEI or Infinity) deployed alongside the gateway with GPU/dynamic batching support.
- **Async Client Conversion**: Refactor [CrossEncoderService](file:///Users/christopherpaul/projects/echo-gate/app/modules/reranking/cross_encoder_service.py) into an async HTTP client (`httpx.AsyncClient`) calling the inference server endpoints.

---

## 4. Cost-Aware Cache Retention (from vCache)
- **Generation Cost Tracking**: Record generation metrics (latency in seconds and token usage/model tier) in the cache entry metadata.
- **Intelligent Eviction**: Weight cache retention to protect expensive responses (e.g. slow reasoning models, complex RAG outputs) from eviction while aggressively evicting fast, cheap responses (e.g. short chitchat, greetings). This maximizes the total compute latency and dollar cost saved per megabyte of cache.

---

## 5. Asynchronous Shadow Audit & Verification Loop
- **Fast, Deterministic Hot Path**: Keep [CacheConfidenceEvaluator.evaluate](file:///Users/christopherpaul/projects/echo-gate/app/modules/llm/cache_confidence.py#L16-L82) synchronous, fast, and binary (hit or miss). No LLM-judge calls on the critical serving path.
- **1%–2% Shadow Audit**: For a small random sample ($p = 0.01$) of semantic cache hits, spawn an asynchronous background task (`asyncio.create_task`) to call the upstream LLM for a fresh answer.
- **LLM Judge**: Compare the cached response against the fresh response using a lightweight, binary equivalence prompt on a cost-effective model (e.g. Gemini 1.5 Flash).
- **Log Ground Truth**: Record evaluation records `(query, cached_entry, similarity, rerank_score, is_correct)` into a PostgreSQL table.
- **Decoupled Periodic Calibration**: Use accumulated audit records in periodic/scheduled jobs (using Conformal Risk Control / CRC) to tune `CACHE_CONFIDENCE_THRESHOLDS` by intent, avoiding the instability of real-time online learning loops.
