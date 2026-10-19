# Data Model

Echo Gate persists relational data in PostgreSQL and cache data in Qdrant. This
document covers both shapes.

## PostgreSQL

PostgreSQL is the only supported relational backend (the app connects with
`psycopg`). There is a single Alembic revision,
`alembic/versions/3f9c1a7b2d40_baseline_postgres_rls.py`, which creates every table,
the non-owner application role, and all Row-Level Security policies. Do not rely on
`create_all`; add a migration for schema changes.

### Tables

#### `api_keys`

Identity table, read before any tenant context exists. **No RLS** — access is
controlled by role grants.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | int PK | |
| `name` | varchar(100) | Human label. |
| `key_prefix` | varchar(16) | Display prefix. |
| `key_hash` | varchar(64) | SHA-256 of the key; **unique**. |
| `tenant_id` | varchar(100) | Bound tenant. |
| `user_id` | varchar(100) NULL | Optional bound user. |
| `is_active` | bool | Revocation flag. |
| `created_at` / `updated_at` | timestamptz | |

#### `gateway_requests`

The trace log: one row per chat request, whether served from cache or provider.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | int PK | |
| `created_at` / `updated_at` | timestamptz | |
| `query_text` | text | Prepared prompt text. |
| `response_text` | text NULL | Assistant content. |
| `routing_decision` | varchar(50) | `LLM`/`REDIS_EXACT`/`QDRANT_EXACT`/`QDRANT_SEMANTIC`/`QDRANT_FAST`. |
| `provider` / `model` | varchar | Effective provider/model (after fallback). |
| `point_id` | varchar(36) | Qdrant point served, if any. |
| `exact_hash` | varchar(64) | Cache key. |
| `intent` | varchar(100) | Classified intent. |
| `core_operation` / `core_subject` / `subject_modifier` / `action_modifier` | varchar(255) | Intent analysis fields. |
| `rerank_score` | numeric(5,4) | Composite rerank score of the top candidate. Recorded for semantic hits (`rerank_score`) and misses (falls back to `top_1_rerank_score`). |
| `score_breakdown` | json | Reranker decomposition: `scoring_mode`, `base_score`, `final_score`, per-signal `factors`, and `details` (`metadata_mismatches` plus per-field `metadata_comparisons` with request/candidate values and overlap). |
| `latency_ms` / `provider_latency_ms` / `cache_lookup_latency_ms` | int | Timing. |
| `latency_breakdown` | json | Per-stage ms map. |
| `user_id` / `tenant_id` / `session_id` / `conversation_id` | varchar | Scope. |
| `evaluation_status` | varchar(20) | `PENDING` default. |
| `llm_relevance_score` / `llm_contradiction_score` / `llm_entailment_score` | numeric(5,4) | Judge scores. |
| `is_false_positive` | bool | Evaluated false-positive flag. |
| `error_message` | text | On failures. |

#### `llm_usage_logs`

Token/cost accounting per provider call.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | int PK | |
| `created_at` / `updated_at` | timestamptz | |
| `service_name` / `model` / `caller_service_name` | varchar | Provider, model, caller (`echo-gate-api`). |
| `user_id` / `tenant_id` / `session_id` / `conversation_id` | varchar | Scope. |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | int | Usage. |
| `cache_read_tokens` / `cache_creation_tokens` | int | Cache accounting. |
| `cost` | numeric(12,6) | Computed dollar cost. |
| `cost_calculated` | bool | Server default `false`; the cost task flips it. |
| `latency_ms` / `status_code` / `is_success` | int/bool | Outcome. |
| `error_message` | text | On failure. |
| `finish_reason` | varchar(50) | |
| `temperature` | numeric(3,2) | |
| `max_tokens` | int | |
| `input_text` / `output_text` | text | Prompt/response text. |
| `extra_metadata` | json | Extras. |

#### `llm_quota_rules`

Optional per-scope rate/token limits. Rows with `tenant_id IS NULL` are global
rules visible to every tenant.

| Column | Type |
| --- | --- |
| `id` | int PK |
| `created_at` / `updated_at` | timestamptz |
| `provider` / `model` | varchar (nullable = wildcard) |
| `user_id` / `tenant_id` | varchar (nullable = wildcard) |
| `max_rpm` / `max_tpm` | int |
| `is_active` | bool |

The service picks the **most restrictive** matching rule. When no rule matches,
defaults are `10` RPM and `20000` TPM (hardcoded in `llm_quota_service.py`).
Counters live in Redis (`rpm:*` 60s window, `tpm:*` 60s window, `budget:*`).
Redis failure degrades to allow.

Rules are managed through `/api/v1/admin/llm-quota` (see
[API_REFERENCE.md](API_REFERENCE.md#llm-quota-admin--apiv1adminllm-quota)). Those
handlers run in `system_rls_context()`, so they cross tenants deliberately and
require an `X-Admin-Key` with a `quota:*` scope.

#### `conversations`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | varchar PK | |
| `tenant_id` | varchar(100) | |
| `user_id` | varchar(100) NULL | |
| `title` | varchar | |
| `created_at` / `updated_at` | timestamptz | |

#### `messages`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | varchar PK | |
| `conversation_id` | varchar FK → `conversations.id` | |
| `tenant_id` | varchar(100) | |
| `user_id` | varchar(100) NULL | |
| `role` | varchar | |
| `content` | text | |
| `created_at` | timestamptz | |

> Conversations/messages exist but their router is not mounted — see
> [API_REFERENCE.md](API_REFERENCE.md#conversations--apiv1conversations-not-mounted).

#### `admin_api_keys`

Scoped operator credentials for `/api/v1/admin/*`. Deliberately a **separate
table** from `api_keys` — see the reasoning below.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | int PK | |
| `name` | varchar(100) | Label for the holder. Not unique. |
| `key_prefix` | varchar(16) | Leading chars of the plaintext, display only. |
| `key_hash` | varchar(64) | SHA-256 hex, **unique**. Plaintext shown once at creation. |
| `scopes` | varchar(255) | Comma-separated: `quota:read`, `quota:write`, `keys:read`, `keys:write`, or `admin:*`. |
| `expires_at` | timestamptz NULL | NULL = no expiry (revocation is explicit). |
| `is_active` | bool | |
| `last_used_at` | timestamptz NULL | Throttled to one write per 5 minutes. |
| `created_at` / `updated_at` | timestamptz | |

**Why a separate table rather than an `is_admin` column on `api_keys`:**

- Tenant keys mint an *identity* and are creatable over HTTP. Admin keys mint
  *privilege* (cross-tenant RLS bypass). The only way to create one is the
  offline `app/scripts/create_admin_key.py` CLI, so the escalation path is
  closed structurally rather than by convention.
- Admin keys are not tenant identities. `TenantContextMiddleware` resolves any
  valid `api_keys` row into a tenant context; an admin key must never become one,
  so it must not live in that table.
- A SQL dump of `api_keys` then leaks tenant credentials only.

Like `api_keys`, this table has **no RLS**: admin key resolution happens before
any tenant context exists, so the row must be readable regardless of the active
tenant. Access control is by key possession and is enforced in
`app/core/guards.py`.

### Indexes

Each table indexes its scope and filter columns (`tenant_id`, `user_id`,
`session_id`, `conversation_id`, `created_at`, etc.). `api_keys.key_hash` is unique.
`gateway_requests` additionally indexes `routing_decision`, `exact_hash`, `intent`,
the four analysis columns, `evaluation_status`, and `is_false_positive`.

### Row-Level Security

The baseline migration provisions role `DB_APP_ROLE` (default `echo_gate_app`) with
`CONNECT`, schema `USAGE`, table DML, and sequence grants, plus default privileges.
RLS is then enabled per table. Every policy begins with
`current_setting('app.rls_system', true) = 'on'`, so trusted background jobs
(`system_rls_context`) bypass isolation.

| Table | Policy shape |
| --- | --- |
| `gateway_requests`, `llm_usage_logs` | **Tenant:** visible when `tenant_id = current_setting('app.current_tenant')`. |
| `llm_quota_rules` | **Quota:** visible when `tenant_id IS NULL` **or** matches the current tenant. |
| `conversations`, `messages` | **User:** visible when tenant matches and `app.current_user = ''` **or** `user_id` matches. |
| `api_keys`, `admin_api_keys` | **No RLS** by design; key resolution happens before a tenant context exists. |

Both `USING` and `WITH CHECK` are set, so cross-tenant inserts are rejected.
Identity is bound per transaction by `app/core/rls.py` as transaction-local GUCs
(`app.current_tenant`, `app.current_user`, `app.rls_system`); unset context makes
all comparisons `NULL` (fail-closed). New rows are stamped with the active
tenant/user when left unset.

---

## Qdrant cache collection

Collection name: **`embeddings_multivector`**
(`app/modules/qdrant/llm_cache_collection_service.py`). Created lazily on first use;
the app calls `ensure_indexes()` at startup.

### Vectors

| Named vector | Type | Purpose |
| --- | --- | --- |
| `prompt_embedding` | dense, COSINE, on_disk | Full-prompt embedding; drives the fast path. |
| `system_prompt_embedding` | dense, COSINE, on_disk | System segment. |
| `user_prompt_embedding` | dense, COSINE, on_disk | User segment. |
| `system_prompt_bm25` | sparse | System lexical. |
| `user_prompt_bm25` | sparse | User lexical. |

- Dense size = `embedding_service.embedding_dimension` (default **384** for
  `all-MiniLM-L6-v2`). A dimension mismatch raises at startup rather than silently
  failing.
- Quantization: INT8 scalar (`quantile=0.99`, `always_ram=True`).
- `on_disk_payload=True`.
- The commented-out `prompt_bm25` sparse vector is intentionally disabled.

### Hybrid query

`query_points_rrf` builds a prefetch list (system dense, system sparse, user dense,
user sparse — each `limit * 2`), then a `FusionQuery(Fusion.RRF)` over them, so
`limit` is `QDRANT_SEARCH_LIMIT` (default `10`). `query_points_dense` performs a
single named-vector query.

### Payload indexes

Created by `ensure_indexes()`: `exact_hash`, `prompt_version`, `model`,
`embedding_model`, `embedding_version`, `cacheable`, `cache_key_version`,
`tenant_id`, `user_id`, `session_id`, `conversation_id`, `scope`, `expires_at`,
`response_format_hash`, `stop_hash`, `completion_tokens`, `entities`, `intent`,
`core_operation`, `core_subject`, `subject_modifier`, `action_modifier`.

### Payload

`QdrantPayload` (`app/modules/qdrant/qdrant_schema.py`) plus merged `metadata`.
Key fields: `prompt`, `system_prompt`, `user_prompt`, `response` (the serialized
`ChatCompletionResponse`), `exact_hash`, `tenant_id`/`user_id`/`session_id`/
`conversation_id`, `model`, `service_name`, `scope`, `entities`,
`time_sensitivity`, `intent`, `core_operation`, `core_subject`,
`embedding_model`, `embedding_version`, `cacheable`, `cache_key_version`,
`created_at`, `expires_at`. Merged metadata adds `prompt_version`,
`intent_prompt_version`, `temperature`, `subject_modifier`, `action_modifier`, and
conditionally `response_format_hash`, `stop_hash`, `completion_tokens`.

### Cache scope

`CacheScope` (`app/modules/qdrant/qdrant_schema.py`):
`GLOBAL`, `TENANT`, `USER`, `SESSION`, `CONVERSATION`. Numeric precedence is in
`SCOPE_HIERARCHY` (`app/core/constants.py`): `GLOBAL(4)` > `TENANT(3)` > `USER(2)` >
`SESSION(1)` > `CONVERSATION(0)`. A query sees entries at its own scope and broader
ones (e.g. a tenant query also sees `GLOBAL`), but never narrower. At write time the
scope derived from request identity is clamped down to the entity extractor's
maximum allowed scope.

### Expiry

`delete_expired()` removes points whose `expires_at < now`. It is scheduled via
Celery Beat (`delete_expired_vectors`). Exact and semantic reads also filter on
`expires_at >= now`.

### Constants (`app/core/constants.py`)

`EMBEDDING_VERSION="v1"`, `CACHE_KEY_VERSION="v1"`, `PROMPT_VERSION="1.0"`,
`INTENT_PROMPT_VERSION="1.0"`, `CALLER_SERVICE_NAME="echo-gate-api"`,
`CACHE_NEGATIVE_TTL_OFFSET=-1000`, and the mock-response sentinel text.
