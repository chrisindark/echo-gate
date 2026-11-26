# Configuration

`api/app/.env.example` is the annotated template an operator copies and edits.
[`ENV_VARS.md`](ENV_VARS.md) is the generated index of every variable the code
reads — including internal ones that are not templated in the example — with type,
default, and source module. `api/app/core/config.py` remains authoritative for
defaults, aliases (`INFERENCE_CLASSIFY_URL` → `INFERENCE_NLI_URL`), and resolution
logic. This page covers what a key/value list cannot: how config loads, what
`CACHE_MODE` means, how the inference platform and retention behave, and which
variables matter operationally.

Copy the example and edit it:

```bash
cp api/app/.env.example api/app/.env.local
```

## How configuration is loaded

1. `api/app/main.py` reads `PYTHON_ENV` (default `local`) and, if
   `api/app/.env.<PYTHON_ENV>` exists, loads it with `python-dotenv`. It then calls
   `load_dotenv()` for a generic `.env` search.
2. `api/app/core/config.py` instantiates a single module-level `config` object that
   reads from the environment. `config.load()` is called again in the FastAPI
   lifespan, so env-file values are in effect at startup. The Celery app
   (`api/app/worker/celery_app.py`) performs the same env-file load.

Anything not set falls back to the default in `config.py`.

> **Boolean flags are strings.** `USE_REDIS_EXACT_MATCHING`,
> `USE_QDRANT_EXACT_MATCHING`, and `USE_QDRANT_SEMANTIC_MATCHING` default to
> `"false"`, so **all cache layers are off unless you set them to the literal
> string `"true"`** (case-insensitive). `USE_FALLBACK_LLM` defaults to `"true"`.

## Cache mode

`CACHE_MODE` is the top-level switch. All three serving-path gates go through
`Config.cache_layer_enabled(layer)`, which returns `False` unless
`CACHE_MODE == "active"` and the underlying service is enabled.

| Mode | Behaviour |
| --- | --- |
| `disabled` | No cache reads, no cache writes. Every request hits the provider. |
| `shadow` | Requests hit the provider; a background task replays the full pipeline, logs what *would* have happened to `shadow_cache_evaluations`, and still writes embeddings + responses to warm the corpus. |
| `active` | Normal serving path: Redis exact → Qdrant exact → semantic, per the `USE_*` flags, with background cache writes. |

Resolution rules (`api/app/core/config.py`):

1. An explicit `CACHE_MODE` (case-insensitive) wins.
2. An unrecognized value logs a warning and falls through to rule 3.
3. When unset, it is derived from the legacy `USE_*` flags: `active` if any is
   `"true"`, otherwise `disabled`. This keeps deployments created before
   `CACHE_MODE` existed working unchanged.

`CACHE_MODE=disabled` disables every cache service regardless of individual flags,
while `shadow` / `active` only initialize the external services enabled by their own
settings. So `CACHE_MODE=shadow` with Redis, Qdrant, and inference all off behaves
like an LLM-only gateway.

## Operational knobs

These are the variables worth knowing by name. For the complete set — including
every prompt-optimization, shadow, audit, telemetry, and webhook knob — see the
generated [`ENV_VARS.md`](ENV_VARS.md) or the annotated `api/app/.env.example`.

### Identity and storage

| Variable | Why it matters |
| --- | --- |
| `DATABASE_URL` | **Must be the non-owner app role** (`echo_gate_app`) so RLS applies. |
| `DATABASE_URL_READ` | Read connection; defaults to `DATABASE_URL`. |
| `MIGRATION_DATABASE_URL` | Owner credentials, read **only by Alembic**, never by the app. |
| `DB_APP_ROLE` / `DB_APP_PASSWORD` | Role the baseline migration provisions. |
| `REDIS_URL`, `QDRANT_URL`, `QDRANT_API_KEY` | Cache backends. |
| `QDRANT_COLLECTION_NAME` | Default `embeddings_multivector`. Point at a migrated collection (`..._v2`) and restart to complete an embedding-model swap. |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Default Redis DB 1. |
| `SUPABASE_URL`, `SUPABASE_AUTH_ENABLED`, `SUPABASE_JWT_AUDIENCE` | Optional Supabase auth and user-key surface. Disabled by default. |

### Providers and routing

| Variable | Why it matters |
| --- | --- |
| `DEFAULT_LLM_SERVICE` | `google-genai` when the request omits a provider. |
| `USE_FALLBACK_LLM`, `FALLBACK_LLM_SERVICE`, `FALLBACK_LLM_MODEL` | Primary→fallback switchover on the blocking and streaming paths. |
| `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `CLAUDE_API_KEY`, `OLLAMA_URL` | Upstream credentials and local endpoint. |
| `LLM_MAX_RETRIES`, `RETRY_BACKOFF_INITIAL_SECONDS`, `RETRY_BACKOFF_MAX_SECONDS` | Retry policy. |
| `ALLOWED_MODELS_JSON`, `MODEL_ALIASES_JSON`, `MODEL_CONTEXT_WINDOWS_JSON` | Runtime allowlist extension, alias routing, and headroom-trim budgets. |

Retries apply only to timeouts/connection errors and HTTP 408/429/500/502/503/504
(`api/app/core/retry.py`). 400/401/403 are fatal and short-circuit the fallback.

### Resilience

| Variable | Default | Why it matters |
| --- | --- | --- |
| `CIRCUIT_BREAKER_ENABLED` | `true` | Per-provider open/half-open/closed breaker. |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Consecutive retryable errors before OPEN. |
| `CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS` | `30` | Wait before a HALF_OPEN probe. |
| `GLOBAL_MAX_CONCURRENCY` | `100` | Global bulkhead; excess load is shed with `429`. |
| `PROVIDER_MAX_CONCURRENCY` | `25` | Per-provider bulkhead. |
| `BULKHEAD_ACQUIRE_TIMEOUT_SECONDS` | `0.0` | How long to wait before shedding. |
| `STREAM_MAX_CONTENT_BYTES` | `2097152` | Guardrail on the streaming accumulator. |
| `MAX_COMPLETION_TOKENS_CAP` | `2000000` | Hard ceiling on caller-supplied `max_tokens`. |

### Inference platform

`INFERENCE_BASE_URL` is how dense embeddings, reranking, relevance, and NLI are
served: they live in `inference-platform/` and answer over HTTP. Nothing is loaded
in-process, so this is not optional — with no engine configured the gateway refuses
to start.

| Variable | Default | Why it matters |
| --- | --- | --- |
| `INFERENCE_BASE_URL` | empty | Required at boot when `INFERENCE_ENABLED` is active. |
| `INFERENCE_*_URL` | derived | Optional per-role overrides (`EMBED`, `RERANK`, `RELEVANCE`, `NLI`). |
| `INFERENCE_*_MODEL` | empty | Pin a model per role; required by gateways that route by model id only (Infinity). |
| `INFERENCE_EMBEDDING_DIM` | `384` | Locked into the Qdrant collection; startup fails when the engine disagrees. |
| `INFERENCE_TIMEOUT_SECONDS` | `5.0` | Per-attempt budget, clamped to `0.1`–`120`. |
| `INFERENCE_MAX_RETRIES` | `1` | Retries for 5xx/throttling/timeouts; a refused connection is never retried. |

- **Strict at boot.** `InferenceClient.verify()` raises when no URL is configured,
  an endpoint is unreachable, a required role is not loaded, or the engine's
  embedding dim / model id disagrees with `INFERENCE_EMBEDDING_DIM` /
  `EMBEDDING_MODEL_NAME`. Without that check the process would answer everything
  from the provider with no cache while `/ready` stayed green.
- **Fail-open at runtime.** An unreachable engine is a cache miss: the provider
  serves the request, `X-Gateway-Degradation` reports `cache_bypassed`, and
  `/health/deps` reports `inference: failed` so the outage pages. Semantic caching
  never fails a request.
- **Roles, not just models.** Two different cross-encoders back `rerank` and
  `relevance`, so requests are routed by role; an unpinned request could otherwise
  score relevance against the wrong model.
- **Score scale is preserved.** The contract returns normalized scores, but the
  gateway consumes `score_raw`/`logits` because thresholds were calibrated on raw
  cross-encoder logits.

### Semantic search and retention

| Variable | Default | Why it matters |
| --- | --- | --- |
| `QDRANT_DENSE_SEARCH_HIGH_THRESHOLD` | `0.98` | Dense score at/above which the fast path serves immediately. |
| `QDRANT_SPARSE_SEARCH_HIGH_THRESHOLD` | `0.33` | Minimum RRF score to be reranked. |
| `QDRANT_SEARCH_LIMIT` | `10` | Candidate pool for the RRF query. |
| `CACHE_CONFIDENCE_THRESHOLDS` | per intent | JSON map of intent → `{high, moderate}` acceptance thresholds (0.85–0.98). |
| `INTENT_TTL_SECONDS` | per intent | JSON map of intent → TTL seconds. |

`time_sensitivity >= 0.9` or intent `finance_market` is never cached.

Value-aware retention (`api/app/modules/qdrant/retention_policy.py`) stretches
TTLs by `(1 + log10(1 + cost * 100)) * (1 + latency_ms / 1000)` up to
`CACHE_RETENTION_MAX_TTL_SECONDS` (30 days). The eviction sweep keeps expired
entries above `CACHE_RETENTION_PROTECT_THRESHOLD` (5.0) for
`CACHE_RETENTION_GRACE_PERIOD_SECONDS` (3 days), and evicts capacity overflow
lowest-value first up to `CACHE_RETENTION_MAX_POINTS` (`0` = disabled). Setting
`CACHE_RETENTION_ENABLED=false` restores the flat `expires_at < now` sweep. A
served hit bumps `hit_count` and `retention_value` out of band.

### Models and version tags

`EMBEDDING_MODEL_NAME` (`BAAI/bge-small-en-v1.5`) is the model the engine is
expected to serve *and* the tag stored on every cached point; semantic search
filters on it, so a swap quarantines old vectors instead of mixing spaces.
`RERANKER_MODEL_NAME` declares the reranker whose raw score scale the confidence
thresholds were calibrated against — recalibrate with
`python -m app.scripts.calibrate_reranker` after any swap, or the gate becomes
always-reject or always-accept.

Version tags (`EMBEDDING_VERSION`, `SPARSE_EMBEDDING_VERSION`, `CACHE_KEY_VERSION`,
`PROMPT_VERSION`, `INTENT_PROMPT_VERSION`, `INTENT_CLASSIFIER_VERSION`,
`CORE_QUERY_EXTRACTOR_VERSION`, `RERANKER_VERSION`, `JUDGE_PROMPT_VERSION`, all
default `v1`) are stored in cache payloads and checked on lookup. Bump one to
invalidate or partition its slice of the cache.

`USE_RERANKER_MODEL`, `USE_RELEVANCE_MODEL`, `USE_NLI_MODEL` (all `true`) disable a
role: the call raises `InferenceUnavailable` instead of hitting the engine, and the
role drops out of the startup check.

### Observability and events

| Variable | Default | Why it matters |
| --- | --- | --- |
| `OTEL_ENABLED` | `true` | Instrumentation on; with `OTEL_EXPORTER_OTLP_ENDPOINT` unset it is a zero-overhead NoOp. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | Set to export spans (Langfuse, Datadog, Jaeger). |
| `TELEMETRY_ENABLED` | `false` | Opt-in anonymous ping (version, generated instance id, deployment mode). Fail-open; never sends prompts, responses, keys, or IPs. |
| `EVENTS_ENABLED` | `true` | Kill switch for the webhook bus. |
| `EVENTS_WEBHOOK_CACHE_MISS`, `..._QUOTA_EXCEEDED`, `..._PROVIDER_FAILURE`, `..._AUDIT_FAILED` | unset | Per-event delivery URL. **No URL means the event is dropped before any dispatch work.** |

Event delivery is `POST {"type", "ts", "payload"}` with a 3-second timeout, through
Celery when the broker is online and a task/daemon thread otherwise. Payloads carry
operational metadata only — never prompt or response text — and credential-shaped
keys are stripped recursively. A cloud overlay replaces the sink with
`app.core.events.set_event_sink()`; see `docs/EXTENSION_SEAMS.md`.

## Templated but not read by the app

`MIGRATION_DATABASE_URL` is consumed by Alembic's `env.py` / `alembic.ini`;
`DIRECT_URL` is the plain-Postgres form used by tooling; and
`INFERENCE_DOCKER_BASE_URL` / `OLLAMA_DOCKER_URL` are Compose interpolation keys for
container-to-container URLs. Conversely, the `ECHO_GATE_*` variables in
`api/app/scripts/` drive the load-test scripts rather than the gateway, so they are
documented in [DEVELOPMENT.md](DEVELOPMENT.md) instead of the template.

If you add an env var, add it to `api/app/core/config.py` and — when an operator sets
it — to `api/app/.env.example`, then run `python scripts/gen_env_docs.py`; CI fails
when `ENV_VARS.md` disagrees with the code.
