# Multi-Region Cache Federation — Design

> **Status:** Design (architecture-only, 2026-11-05). This is the record
> required by PRIORITIES.md §P1 so the Qdrant collection schema does not
> accidentally add region-incompatible assumptions. It ships no behavior:
> replication is disabled until implemented off these seams.

## Goals & non-goals

- Each region runs its own full cache stack (Redis + Qdrant + workers) and
  answers from it local-first. Regions never synchronously query each other
  on the serving path.
- High-value entries replicate asynchronously across regions. Everything
  else is a cache miss → provider → background write, exactly as today.
- Tenant isolation is preserved across federation: a replicated entry can
  never widen its scope, and scope is enforced at read time by the same
  Qdrant filters used today.

## What "local-first" means

1. Request hits Redis exact → Qdrant exact → Qdrant fast-path → hybrid RRF →
   confidence gate, all local, all fail-open as today.
2. Miss → provider call (resilience path unchanged: retries, circuit breaker,
   fallback) → `_background_save_cache` writes locally.
3. The background write is the only federation touchpoint: it enqueues a
   replication task for high-value entries (below) and returns.

No cross-region reads on the hot path. The round-trip savings model that
makes federation worth it: a local cache hit is ~1–10 ms and $0; a cross-
region hit would add network latency and a remote failure mode to every
request. Replicating the *stock* (high-value entries) beats querying the
*flow* (live cross-region lookups).

## What replicates

Value is already recorded on every point: `provider_cost`, `latency_ms`,
`token_count`, `hit_count`, and the derived `retention_value` +
`CACHE_RETENTION_PROTECT_THRESHOLD` (see
`api/app/modules/qdrant/retention_policy.py` and ARCHITECTURE.md §Value-aware
retention). Federation reuses that:

| Replicates | Criteria |
| --- | --- |
| **High-value points** | `retention_value >= CACHE_FEDERATION_MIN_RETENTION_VALUE` (new knob, default tuned so only expensive/slow-heavy entries qualify) **and** `cacheable == True`. |
| **Exact-match payloads** | Redis exact entries whose cost × hit-count clears the same value bar, exported via the existing `POST /api/v1/admin/cache/search` (semantic & `mode: "exact"`) + entry inspector primitives. |

Never replicates: `cacheable=False` points (time-sensitive/finance), points
with `scope` narrower than `GLOBAL`/`TENANT` (see Privacy below), and
anything in `CACHE_MODE=shadow` (shadow bodies must not leave the region).

## Privacy & tenancy

Cache entries are already tenant-scoped at write time (entity-extraction
clamps `save_to_qdrant_cache` to the allowed scope) and filtered at read
time (`scope` + `tenant_id` in the Qdrant filter). Federation inherits both
— a replicated point carries its original `scope`/`tenant_id`, so a
`TENANT`-scoped entry can only serve that tenant in the peer region.

Rules:

- Only `GLOBAL` and `TENANT` scope may replicate. `USER`, `SESSION`, and
  `CONVERSATION` scope never leave the generating region (privacy boundary
  enforced by the scope hierarchy, not a config toggle).
- Cross-tenant reads stay impossible: the scope/tenant filter is applied at
  query time in every region identically.

## Topology

```text
                ┌───────────────── Redis ──────────────┐
                │  Redis exact (local-first cache)      │
Region A ────────│  Qdrant embeddings_multivector       │
(eg. us-east)    │  Celery worker + beat                │
                └────────▲───────────────▲─────────────┘
                         │ replication     │ replication
                         │ task (Celery)   │ task (Celery)
                ┌────────┴─────────────────┴─────────────┐
Region B ────────│  Redis exact (local-first cache)      │
(eg. eu-west)    │  Qdrant embeddings_multivector       │
                │  Celery worker + beat                │
                └────────────────────────────────────────┘
```

- Writes are local-first; replication is a Celery task
  (`replicate_cache_entry`, signature below) that upserts the point into
  peer Qdrant collections and warms peer Redis.
- Peers are configured as additional `QDRANT_URL`s + a replication endpoint
  (admin `POST /api/v1/admin/cache/replicate`, or a direct Qdrant bulk
  import over the existing client). In the first implementation the
  replication channel is the admin API — signed (`X-Admin-Key` with
  `cache:write`) and audited like `/cache/warmup`.

## Concrete seams (to implement against)

### Config (all new, all default-off for federation)

| Env | Default | Meaning |
| --- | --- | --- |
| `CACHE_FEDERATION_ENABLED` | `false` | Master switch. Off ⇒ zero behavior change. |
| `CACHE_FEDERATION_PEERS` | `""` | Comma-separated peer gateway base URLs (or `qdrant_url:regions`). |
| `CACHE_FEDERATION_MIN_RETENTION_VALUE` | e.g. `5` | Replicate only points at/above this `retention_value`. |
| `CACHE_FEDERATION_ENDPOINT` | `""` | Base URL of the ingest endpoint on peer regions. |

### Payload/index additions (schema, not behavior)

- Qdrant payload gains `region_origin` (keyword) and `federation_version`
  (keyword), so peers can attribute provenance and ignore
  schema-skewed points. Additive; existing points simply lack the fields.
- `ensure_indexes()` (LlmCacheCollectionService) indexes the two new fields
  alongside `tenant_id`/`scope`. Additive idempotent creation, same as
  today.

### Replication task signature

```python
# api/app/modules/qdrant/tasks.py  (new)
@celery_app.task(name="replicate_cache_entry")
def replicate_cache_entry(task, point_id: str) -> None: ...
```

- Input: point id + region origin, resolved locally from Qdrant.
- Output: one upsert per peer (Qdrant point + Redis exact key), retried by
  Celery's existing retry policy. Fail-open: a peer down ⇒ retries ⇒
  dropped; local serving is never affected.
- Enqueued from `_background_save_cache` after a successful local write,
  only when `CACHE_FEDERATION_ENABLED` and the point clears the value bar.

## Scheduling (Celery beat)

- Daily high-water sweep (beat, e.g. `CACHE_FEDERATION_SWEEP_INTERVAL_SECONDS`,
  default 86400): scan for points past the value bar inserted since the last
  sweep and enqueue any not yet replicated, so missed enqueue attempts
  self-heal.
- Conflict policy: last-writer-wins on `point_id` (idempotent upsert). TTL
  and expiry are recomputed on the peer using the same retention policy, so
  a replicated entry cannot outlive its intent TTL.

## Invariants

1. **Fail-open** — federation adds no request-path dependency. A peer outage
   is a cache-miss-class event, never a 5xx.
2. **Tenant isolation** — scope/tenant filters are applied identically in
   every region; cross-region replication cannot widen scope.
3. **No in-process embeddings** — peer ingestion reuses the existing
   upsert path (`LlmCacheCollectionService`); no vector recomputation.
4. **Known gaps are recorded, not fixed silently** — this doc is the
   record; implementations add new gaps here when discovered.

## Implementation order

1. Config + schema seam (payload/index) — this PR.
2. Replication task + ingestion endpoint (admin-scoped).
3. Enqueue from `_background_save_cache` + daily sweep.
4. Shadow-verify (like `CACHE_MODE=shadow`): log would-be replications for
   a week before enabling cross-region writes.
