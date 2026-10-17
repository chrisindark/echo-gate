# Echo Gate Implementation Plans

This document tracks the approved, high-ROI architectural implementations for Echo Gate. Speculative, overly complex, or empirically disproven mechanisms (such as per-entry Bayesian logistic regression loops and live online threshold mutations) have been removed in favor of robust, decoupled designs.

---


## 3. High-Performance Model Serving Architecture
- **Strip Heavy In-Process ML Dependencies**: Remove `sentence-transformers`, `transformers`, and `torch` from the core gateway container dependencies, reducing Docker image size from ~5GB+ to <300MB for faster startup and horizontal autoscaling.
- **Dedicated Inference Server**: Offload embeddings and cross-encoder reranking to a dedicated model server (e.g. Text Embeddings Inference / TEI or Infinity) deployed alongside the gateway with GPU/dynamic batching support.
- **Async Client Conversion**: Refactor [CrossEncoderService](file:///Users/christopherpaul/projects/echo-gate/app/modules/verifier/cross_encoder_service.py) into an async HTTP client (`httpx.AsyncClient`) calling the inference server endpoints. (Note: `RerankerService` in `app/modules/reranking/` imports this service, so both consumers must move off the in-process model.)

---

## 4. Cost-Aware Cache Retention (from vCache)
- **Generation Cost Tracking**: Record generation metrics (latency in seconds and token usage/model tier) in the cache entry metadata.
- **Intelligent Eviction**: Weight cache retention to protect expensive responses (e.g. slow reasoning models, complex RAG outputs) from eviction while aggressively evicting fast, cheap responses (e.g. short chitchat, greetings). This maximizes the total compute latency and dollar cost saved per megabyte of cache.

---

## 5. Shadow Audit & Calibration

> Partially implemented: the LLM judge (`app/modules/verifier/judge_service.py`), the `PENDING → EVALUATED` trace pipeline (`gateway_requests`), and the periodic `evaluate_pending_requests` Celery task are live. Today that task runs the judge over the logged response of **every** non-error trace; there is no sampled *shadow* call and no data-driven threshold calibration. The remaining work is below.

- **Sampled Shadow Audit**: For a small random sample ($p \approx 0.01$–$0.02$) of **semantic cache hits**, spawn an asynchronous background task (`asyncio.create_task`) to fetch a **fresh upstream answer** for the same request. The current judge scores the cached response in isolation, with no fresh answer to compare against.
- **Comparative LLM Judge**: Compare the cached response against the fresh answer with a lightweight equivalence prompt on a cost-effective model (extending the existing relevance/contradiction/entailment scoring so it can also detect silent false-positive hits).
- **Audit Ground-Truth Store**: Persist `(query, cached_entry, similarity, rerank_score, is_correct)` to a dedicated PostgreSQL table. The trace row already carries `rerank_score`, `llm_relevance_score`, and `is_false_positive`, but not the paired fresh answer needed for calibration.
- **Decoupled Periodic Calibration**: Tune `CACHE_CONFIDENCE_THRESHOLDS` by intent from accumulated audit records in offline/scheduled jobs (e.g. Conformal Risk Control), avoiding real-time online threshold mutation. Note: `app/scripts/calibrate_reranker.py` currently sweeps thresholds from the static `app/scripts/test_dataset.py`, not from live audit records.

---

## 6. Provider Resilience & Single-Point-of-Failure Hardening

Echo Gate sits in-line for every service and agent that calls it, so its failure mode must be *degraded*, not *down*. The app tier is already stateless (all state lives in Postgres/Redis/Qdrant; the process holds only DI singletons and loaded ML models), which makes horizontal scaling the primary mitigation. The gaps below are what stand between today's fail-open cache design and a genuinely resilient gateway.

- **Tiered health probes**: `GET /api/v1/health/ready` currently returns `503` when Redis, Qdrant, *or* Postgres fail (`app/modules/health/health_service.py`). If the orchestrator gates load-balancer membership on it, a cache blip evicts every replica and turns graceful degradation into an outage. Split into `/live` (process only), `/ready` (the minimum needed to serve a correct response), and a non-gating `/health/deps` used for alerting only.
- **Per-provider circuit breaker**: There is no breaker today — a dead provider means every request pays the full `LLM_PROVIDER_TIMEOUT_SECONDS` wait and eats a fallback attempt, hammering the upstream and holding event-loop slots. Add open/half-open/closed state keyed by provider (optionally provider+model), tripping on consecutive retryable failures from `RETRYABLE_STATUS_CODES` in `app/core/retry.py`.
- **Bulkheads and load shedding**: There is no global or per-provider concurrency cap (only per-API-key quota in `llm_quota_guard`). Add a max-in-flight semaphore plus per-provider caps so one slow provider cannot starve all tenants, and shed excess load with `429`/`503` rather than collapsing.
- **Explicit degradation contract**: Formalize and document the states — full → cache-degraded (provider pass-through) → provider-fallback (alternate model) → hard-down `503` — and confirm every cache/Redis/Qdrant failure path stays fail-open (existing invariant).
- **Resilient client guidance**: Provide/document a retrying client with its own breaker and a documented bypass path (e.g. direct provider call or queue-and-replay) for when the gateway itself is unavailable.
- **Config & docs**: Add all new knobs to `app/core/config.py` and `docs/CONFIGURATION.md`.

---

## 7. MCP Server for Agent & IDE Consumers

Expose Echo Gate as an MCP server so agentic clients and IDE harnesses can discover and call it. This is an **additional surface**, not a replacement: the OpenAI-compatible `POST /api/v1/chat/completions` API stays for service-to-service callers, and it is separate from Echo Gate acting as a *client* to LLM providers.

- **Transport**: Mount a Streamable-HTTP MCP endpoint (e.g. `/mcp`) alongside the REST routers in `app/main.py`, reusing `DependencyContainer`, the correlation/user/tenant middlewares, `llm_quota_guard`, and the RLS context.
- **Tools**: `chat_completion`, `list_models`, `get_cache_stats`, `flush_cache`, `check_quota`, `get_usage`.
- **Resources**: gateway traces (`gateway_requests`), usage/analytics aggregates, and Qdrant cache contents.
- **Auth and tenancy first**: Map MCP client credentials → API key → tenant/user through the existing `api_keys` service and `TenantContextMiddleware`. Never open a second unauthenticated path into tenant data.
- **Streaming**: Support SSE for MCP where the transport allows it, reusing the existing streaming pipeline.
- **Non-blocking**: Keep MCP off the synchronous serving path; it is additive and must not affect latency or correctness for existing consumers.
- **Dependency note**: the `mcp` package is already present in `poetry.lock` (pulled in as a dependency extra), so this is low-friction to prototype.
