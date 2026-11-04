---
name: cache-pipeline
description: Architecture and debugging guidelines for Echo Gate's multi-tier caching pipeline (Redis exact match, Qdrant hybrid BM25 + dense vector search, reranking, and shadow mode).
---

# Multi-Tier Semantic Caching Pipeline

Echo Gate implements a multi-tier cache to maximize cache hits, reduce inference latency, and eliminate hallucinations.

## 1. Pipeline Stages
1. **Layer 1: Redis Exact Match**:
   - Sub-millisecond lookup via SHA-256 hash of normalized prompt + model configuration.
   - Guarded by `Config.cache_layer_enabled("redis")`.
2. **Layer 2: Qdrant Exact Hash**:
   - Secondary persistent exact hash check if Redis key expired or evicted.
3. **Layer 3: Qdrant Hybrid Semantic Search**:
   - Dual-vector search combining dense embeddings (`prompt_embedding` via `inference-platform/`) and sparse lexical tokens (BM25).
   - Reciprocal Rank Fusion (RRF) combines candidate lists.
   - **High-Confidence Bypass**: When cosine similarity $\ge$ `QDRANT_DENSE_SEARCH_HIGH_THRESHOLD` (default 0.98), skip expensive reranker and return fast-path hit.
4. **Asynchronous Cache Write & Shadow Evaluation**:
   - Cache misses answer immediately from the provider; cache vectorization and insertion occur asynchronously via Celery or background tasks.
   - In `CACHE_MODE=shadow`, provider always serves the client while the full cache pipeline evaluates in the background to warm the cache and measure hit rates.

## 2. Invariants
- **Fail-Open Policy**: If Redis, Qdrant, or `inference-platform/` fails or is unreachable, degrade to the direct provider answer (`mark_cache_degraded`). Never raise a 5xx to the client on cache outages.
- **Tenant Scope & RLS**: Cache entries are strictly tenant-scoped. Sensitive prompts must not be stored at `GLOBAL` scope. Time-sensitive queries (`time_sensitivity >= 0.9`) are not cacheable.
- **No In-Process Embeddings**: Dense embeddings and reranking route over HTTP to `inference-platform/`. Never import heavy ML frameworks into the gateway.

## 3. Verification
- Test cache isolation & failure handling: `poetry run pytest api/app/modules/llm/tests/test_cache_write_failure_isolation.py`
- Test Qdrant module: `poetry run pytest api/app/modules/qdrant/tests/`
