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

- **Transitive Dependency Pin (Pillow via fastembed)**: `fastembed 0.8.1` pins `pillow<13.0,>=12.0.0`, holding Pillow at `12.3.0` while upstream Pillow 13.0.0 is released. Echo Gate uses `fastembed` exclusively for local sparse BM25 text embedding (`SparseTextEmbedding`); image decoding/ImageEmbedding code paths are never executed. Monitored via `scripts/audit_python.py` until `fastembed` bumps its Pillow upper bound.
- **Unpatchable dev-tooling advisory (picomatch <= 4.0.7, GHSA-fc5q-39xc-vjpf)**: `web/` build tooling (eslint, `fast-glob`, `lint-staged`, `shadcn`) depends on `picomatch`, which has a high-severity ReDoS advisory and **no patched release published upstream** (all versions through 4.0.7 are affected). The `shadcn` CLI is a dev dependency, not a runtime one, so `npm audit --omit=dev` is clean. Tracked as an explicit exemption in `scripts/audit_node.py`; remove it when picomatch publishes a fix.
- When new discrepancies or temporary compromises are discovered, document them here immediately rather than fixing them silently in passing.
