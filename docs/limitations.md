# Echo Gate Limitations & Known Gaps

This document catalogs intentional architectural boundaries, deferred capabilities, and known gaps in Echo Gate. It serves as an anti-hallucination guardrail for coding agents.

## 1. Intentional Architectural Boundaries

### No In-Process Model Embeddings
Echo Gate does not run sentence-transformers or local embedding models in-process. All dense vectorization, reranking, and NLI tasks must route over HTTP to `inference-platform/` (or an Infinity instance). In-process fallback is deliberately omitted to keep container images lean (~0.7 GB vs ~9.4 GB).

### Fail-Open Cache Policy
If Redis, Qdrant, or the inference platform is unavailable, Echo Gate degrades gracefully to direct LLM provider execution (`mark_cache_degraded`). It will never return a 5xx error to clients due to internal cache outages.

### Postgres Non-Owner Connection
The application connects to PostgreSQL using a non-owner role to guarantee that PostgreSQL Row-Level Security (RLS) policies are strictly enforced for tenant isolation.

---

## 2. Deferred Capabilities

- **Multi-Region Cache Replication**: Cross-region replication of cached entries is designed but deferred to Phase 4.
- **Hosted Cloud Billing**: Billing endpoints (`/api/v1/billing/*`) are reserved for commercial deployment and intentionally excluded from the open-source gateway.

---

## 3. Known Gaps & Discrepancies

- **No Open Critical Gaps**: All former gaps (conversation router unmount, rate-limit buckets, Celery beat schedules) have been resolved and verified against tests.
- When new discrepancies or temporary compromises are discovered, document them here immediately rather than fixing them silently in passing.
