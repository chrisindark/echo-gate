# ADR 0001: Fail-Open Semantic Caching Policy

## Status
Accepted (2026-10-15)

## Context
Echo Gate acts as an inline gateway for LLM chat completion requests. Caching is an optimization to reduce latency and token spend, but the gateway's core availability promise is that callers must receive model completions when the underlying LLM provider is up.

## Decision
1. If Redis, Qdrant, or the vector embedding service fails or times out, the cache lookup must degrade immediately to the direct upstream provider response.
2. The failure is recorded in diagnostics (`mark_cache_degraded`), but client requests never return 5xx errors due to internal cache issues.
3. Cache writes and vector embeddings on cache misses run asynchronously out-of-band; persistence failures do not delay or fail client responses.

## Consequences
- Cache outages never cause user-facing request downtime.
- Monitoring and telemetry must surface degraded cache states via `/api/v1/health/deps`.
