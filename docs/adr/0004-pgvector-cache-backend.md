# ADR 0004: Optional pgvector Cache Backend (dense-first)

## Status
Proposed (2026-11-20) — tracked for development; not yet implemented.

## Context

Echo Gate's semantic cache today is Qdrant-only: a 3-named-dense-vector collection with sparse BM25 vectors and single-query RRF fusion, plus payload-indexed filters and value-aware retention. Some self-hosters want to avoid running a separate vector database, and pgvector is a plausible way to keep the cache inside the Postgres instance they already run.

A literal "drop-in" is not possible: Qdrant's sparse-vector storage, single-query RRF, scalar INT8 quantization, and independent failure domain are load-bearing for the current pipeline and tuning.

## Decision

1. **Add an optional cache backend selected by `CACHE_BACKEND` (`qdrant` default, `pgvector` opt-in), extending the existing `StorageFactory` seam (`api/app/core/storage.py`) rather than forking call sites.** Qdrant remains the reference backend.
2. **Phase 1 is dense-first.** The pgvector backend implements exact match, dense fast path (`QDRANT_DENSE_SEARCH_HIGH_THRESHOLD`-equivalent cosine gate), dense semantic search feeding the reranker, upsert, record-hit, delete, and value-aware eviction/retention. **Hybrid (sparse BM25 + RRF) is explicitly out of scope for Phase 1** and is documented as unavailable in that mode (lower expected hit rate on identifier/entity-heavy prompts).
3. **Backend-specific configuration.** Thresholds, quantization, and index parameters are not shared between backends; each backend owns its own knobs. Dense cosine is comparable across backends; RRF/fusion scores are not.
4. **No in-process embeddings.** Dense vectors continue to come from `inference-platform/` over HTTP; sparse BM25 (when added in Phase 2) must reuse the existing fastembed tokenizer contract or a versioned replacement.
5. **Fail-open is preserved at the port boundary.** Backend errors call `mark_cache_degraded` and degrade to a provider answer, exactly as today.
6. **Known limitations recorded, not fixed silently:** pgvector mode lacks hybrid retrieval in Phase 1; cache load shares the primary Postgres (or a second instance) and is not independently degradable unless a separate database is used; RLS policy for `GLOBAL`-scoped entries plus Celery background writes must be designed carefully; high-churn cache tables require autovacuum tuning; HNSW index dimension ceiling (2000 for `vector`) must be respected on embedding-model changes.

## Consequences

- Self-hosters can run on Postgres-only with documented reduced hit rate.
- Two retrieval stacks are maintained; parity (hybrid + federation) is a separate, later decision (Phase 2+) and is not implied by this ADR.
- Switching backends starts the sparse cache cold; dense vectors remain portable via `app/scripts/migrate_embeddings.py`.
- Admin/diagnostics surface (`/api/v1/qdrant/*`) stays Qdrant-scoped in Phase 1; pgvector gets its own endpoints or a backend-neutral rename in a later slice.

## Non-goals

- Replacing Qdrant as the default.
- Claiming behavioral equivalence between backends.
- Implementing multi-region federation for pgvector (the design in `docs/MULTI_REGION_CACHE_FEDERATION.md` is Qdrant-specific until revisited).
