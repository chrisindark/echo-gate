# Configuration

## How configuration is loaded

1. `api/app/main.py` reads `PYTHON_ENV` (default `local`) and, if
   `api/app/.env.<PYTHON_ENV>` exists, loads it with `python-dotenv`. It then calls
   `load_dotenv()` for a generic `.env` search.
2. `api/app/core/config.py` instantiates a single module-level `config` object that
   reads from the environment. `config.load()` is called again in the FastAPI
   lifespan, so values from the env file are in effect at startup. The Celery app
   (`api/app/worker/celery_app.py`) performs the same env-file load.

Because values are read from the environment, anything not set falls back to the
default in `config.py`. Copy `api/app/.env.example` to `api/app/.env.local` (or
`api/app/.env.<PYTHON_ENV>`) to start.

> **Enabled-by-default flags are strings.** `USE_REDIS_EXACT_MATCHING`,
> `USE_QDRANT_EXACT_MATCHING`, and `USE_QDRANT_SEMANTIC_MATCHING` default to
> `"false"`, so **all cache layers are off unless you set them to the literal
> string `"true"`** (case-insensitive). `USE_FALLBACK_LLM` defaults to `"true"`.

## App

| Variable | Default | Purpose |
| --- | --- | --- |
| `PYTHON_ENV` | `local` | Selects `api/app/.env.<PYTHON_ENV>`; also affects log level/format. |
| `PROJECT_NAME` | `""` | App title. |
| `VERSION` | `0.1.0` | Reported version. |
| `SERVICE_NAME` | `""` | Added to structured log records. |
| `LOG_LEVEL` | env-dependent | Explicit log level override. |
| `LOG_FORMAT` | `json` | `json` or colored text. |
| `COLORIZE_JSON` | unset | Force colorization. |

## Provider endpoints and keys

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | unset | OpenAI auth. |
| `GEMINI_API_KEY` | unset | Google GenAI auth. |
| `GROQ_API_KEY` | unset | Groq auth. |
| `OPENROUTER_API_KEY` | unset | OpenRouter auth. |
| `CLAUDE_API_KEY` | unset | Claude (Anthropic) auth. |
| `OLLAMA_URL` | `http://localhost:11434` | Local Ollama base URL. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1/chat/completions` | OpenAI endpoint. |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1/chat/completions` | Groq endpoint. |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1/chat/completions` | OpenRouter endpoint. |
| `CLAUDE_BASE_URL` | `https://api.anthropic.com/v1/messages` | Claude (Anthropic) messages endpoint. |

## Storage connections

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://echo_gate_app:echo_gate_app@localhost:5432/echo_gate` | Write connection. **Must be the non-owner app role** so RLS applies. |
| `DATABASE_URL_READ` | `DATABASE_URL` | Read connection. |
| `MIGRATION_DATABASE_URL` | none in config | Owner credentials used **only by Alembic**. Not read by the app. |
| `DB_APP_ROLE` | `echo_gate_app` | Role the baseline migration provisions; used by Alembic. |
| `DB_APP_PASSWORD` | `echo_gate_app` | Password for that role. |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis (exact cache + quota counters). |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant HTTP endpoint. |
| `QDRANT_API_KEY` | unset | Qdrant Cloud auth. |
| `QDRANT_REPLICATION_FACTOR` | unset | Collection replication factor (int). |
| `QDRANT_WRITE_CONSISTENCY_FACTOR` | unset | Write consistency factor (int). |
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` | Celery broker. |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/1` | Celery result backend. |

### Database pool

| Variable | Default |
| --- | --- |
| `DB_POOL_SIZE` | `20` |
| `DB_MAX_OVERFLOW` | `10` |
| `DB_POOL_TIMEOUT` | `30` |
| `DB_POOL_RECYCLE` | `1800` |
| `DB_POOL_PRE_PING` | `true` |

## Routing, fallback, and retries

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_LLM_SERVICE` | `google-genai` | Provider when the request omits one. |
| `USE_FALLBACK_LLM` | `true` | Enable fallback provider on primary failure. |
| `FALLBACK_LLM_SERVICE` | `google-genai` | Fallback provider. |
| `FALLBACK_LLM_MODEL` | `gemini-3.1-flash-lite` | Fallback model. |
| `LLM_PROVIDER_TIMEOUT_SECONDS` | `15.0` | Per provider attempt timeout. |
| `HTTP_CLIENT_TIMEOUT_SECONDS` | `120.0` | Shared HTTP client timeout. |
| `LLM_MAX_RETRIES` | `0` | Retries per provider call (`stop_after_attempt(max_retries + 1)`). |
| `RETRY_BACKOFF_INITIAL_SECONDS` | `1` | Initial exponential-jitter backoff. |
| `RETRY_BACKOFF_MAX_SECONDS` | `10` | Max backoff. |
| `RETRY_AFTER_DEFAULT_SECONDS` | `5` | `Retry-After` advertised when every candidate provider failed and no circuit breaker is counting down. |
| `CIRCUIT_BREAKER_ENABLED` | `true` | Enable per-provider circuit breakers. |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Consecutive retryable errors before a provider trips OPEN. |
| `CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS` | `30.0` | Recovery window in seconds before attempting a HALF_OPEN probe. |
| `CIRCUIT_BREAKER_HALF_OPEN_SUCCESS_THRESHOLD` | `2` | Consecutive probe successes in HALF_OPEN to close the breaker. |
| `GLOBAL_MAX_CONCURRENCY` | `100` | Global concurrency bulkhead limit across all upstream requests. |
| `PROVIDER_MAX_CONCURRENCY` | `25` | Maximum concurrent in-flight requests permitted per provider. |
| `BULKHEAD_ACQUIRE_TIMEOUT_SECONDS` | `0.0` | Maximum wait in seconds before shedding load with HTTP 429. |
| `ALLOWED_MODELS_JSON` | unset | JSON string of `{provider: [models...]}` to override/extend allowed models dynamically. |
| `MODEL_ALIASES_JSON` | unset | JSON string of `{alias: [{service, model}, ...]}` mapping logical aliases (e.g. `fast`, `cheap`) to ranked provider/model pairs. |
| `STREAM_MAX_CONTENT_BYTES` | `2097152` | Guardrail memory limit (bytes) on streaming response accumulator before skipping cache persistence. |

Retries apply only to timeouts/connection errors and HTTP 408/429/500/502/503/504
(`app/core/retry.py`). HTTP 400/401/403/500 are fatal and short-circuit the
fallback entirely.

## Cache layers

`CACHE_MODE` selects how the cache pipeline is allowed to affect serving.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CACHE_MODE` | derived (see below) | `disabled`, `shadow`, or `active`. Only `active` lets the cache answer requests. |
| `USE_REDIS_EXACT_MATCHING` | `false` | Enable the Redis exact cache (layer 1). |
| `REDIS_TTL` | `3600` | Default Redis TTL (seconds). |
| `USE_QDRANT_EXACT_MATCHING` | `false` | Enable the Qdrant exact cache (layer 2). |
| `USE_QDRANT_SEMANTIC_MATCHING` | `false` | Enable the semantic cache (layer 3). |

`CACHE_MODE` values:

| Mode | Behaviour |
| --- | --- |
| `disabled` | No cache reads and no cache writes. Requests always hit the provider. |
| `shadow` | Requests always hit the provider; a background task replays the whole pipeline, logs what *would* have happened to `shadow_cache_evaluations`, and still writes embeddings + responses to Redis/Qdrant to warm the corpus. |
| `active` | Normal serving path: Redis exact → Qdrant exact → semantic, per the `USE_*` flags, with background cache writes. |

Resolution rules (`app/core/config.py`):

1. An explicit `CACHE_MODE` (case-insensitive) wins.
2. An unrecognized value logs a warning and falls through to rule 3.
3. When unset, the value is derived from the legacy `USE_*` flags: `active` if
   any of `USE_REDIS_EXACT_MATCHING`, `USE_QDRANT_EXACT_MATCHING`, or
   `USE_QDRANT_SEMANTIC_MATCHING` is `"true"`, otherwise `disabled`. This keeps
   deployments created before `CACHE_MODE` existed working unchanged.

All three serving-path gates go through `Config.cache_layer_enabled(layer)`,
which returns `False` unless `CACHE_MODE == "active"`. Background and shadow
callers bypass those gates explicitly (`force=True`) so `shadow` mode can
exercise every layer while `disabled`/`shadow` never serve a cached answer.

In `active` mode the three `USE_*` flags still choose which layers are enabled.
In `shadow` mode every layer runs in the background regardless of the flags, so
`shadow` supersedes them for observation; leave the flags unset until you flip to
`active`.

### Shadow pipeline tuning

| Variable | Default | Purpose |
| --- | --- | --- |
| `SHADOW_SAMPLE_RATE` | `1.0` | Fraction (0–1) of provider responses replayed through the shadow pipeline. Lower it to cap judge/embedding cost on high-volume deployments. |
| `SHADOW_JUDGE_ENABLED` | `true` | Run the LLM equivalence judge when a shadow match is found; the judge is what populates `is_equivalent`. |
| `SHADOW_READINESS_MIN_EVALUATIONS` | `200` | Evaluations required before `/api/v1/shadow-cache/summary` can report `READY`. |
| `SHADOW_READINESS_MIN_HIT_RATE` | `0.05` | Would-be hit rate floor for `READY`. |
| `SHADOW_READINESS_MIN_EQUIVALENCE_RATE` | `0.98` | Judge-equivalence floor for `READY`. |

`SHADOW_*` settings are read even when the mode is not `shadow`; they only take
effect while shadow evaluation runs.

### Cache audit loop tuning (Phase 2)

In `CACHE_MODE=active`, a low-frequency asynchronous audit captures unbiased ground-truth comparisons between approved semantic cache hits and fresh provider answers.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CACHE_AUDIT_ENABLED` | `true` | Enable the out-of-band shadow audit loop on approved semantic cache hits. |
| `CACHE_AUDIT_SAMPLE_RATE` | `0.02` | Fraction (0–1) of semantic cache hits sampled for fresh provider comparison. Default 2% (p ≈ 0.02) to keep LLM audit cost < 0.05% of serving spend. |
| `CACHE_AUDIT_EVICT_ON_FAILURE` | `true` | Instant eviction kill switch: if the judge rates the cached response non-equivalent to the fresh completion, immediately delete the Qdrant point and Redis exact key. |
| `CACHE_CALIBRATION_ALPHA` | `0.02` | Target maximum error rate for high-confidence threshold calibration (e.g. 0.02 = 2% error rate / 98% precision guarantee). |
| `CACHE_CALIBRATION_MODERATE_ALPHA` | `0.05` | Target maximum error rate for moderate-confidence threshold calibration (e.g. 0.05 = 5% error rate). |
| `CACHE_CALIBRATION_MIN_SAMPLES` | `10` | Minimum ground-truth audit evaluations required before an intent's thresholds are overridden. |

## Shared inference platform

`INFERENCE_BASE_URL` moves dense embeddings, reranking, relevance and NLI out of
this process and into `inference-platform/`, which serves them over HTTP to the
gateway, doc-nexus, and agent-forge alike. Empty (the default) keeps every model
in-process, so existing deployments are unchanged until they opt in.

| Variable | Default | Purpose |
| --- | --- | --- |
| `INFERENCE_BASE_URL` | *(empty = disabled)* | Base URL of the inference engine, e.g. `http://localhost:7997`. Setting it enables remote mode. |
| `INFERENCE_EMBED_URL` | *(empty = fallback)* | Optional override for dense embeddings route (`/embeddings`). |
| `INFERENCE_RERANK_URL` | *(empty = fallback)* | Optional override for primary reranking route (`/rerank`). |
| `INFERENCE_RELEVANCE_URL` | *(empty = fallback)* | Optional override for relevance scoring (defaults to `INFERENCE_RERANK_URL`). |
| `INFERENCE_NLI_URL` | *(empty = fallback)* | Optional override for NLI / classification (`/classify`). `INFERENCE_CLASSIFY_URL` accepted as alias. |
| `INFERENCE_TIMEOUT_SECONDS` | `5.0` | Default per-attempt request budget. Clamped to `0.1`-`120`. |
| `INFERENCE_EMBED_TIMEOUT_SECONDS` | `5.0` | Optional timeout specifically for `/embeddings`. |
| `INFERENCE_RERANK_TIMEOUT_SECONDS` | `5.0` | Optional timeout specifically for `/rerank`. |
| `INFERENCE_RELEVANCE_TIMEOUT_SECONDS` | `5.0` | Optional timeout specifically for relevance `/rerank`. |
| `INFERENCE_NLI_TIMEOUT_SECONDS` | `5.0` | Optional timeout specifically for `/classify`. |
| `INFERENCE_MAX_RETRIES` | `1` | Retries for transient faults (5xx, throttling, timeouts). A refused connection is never retried. Clamped to `0`-`5`. |
| `INFERENCE_EMBEDDING_DIM` | `384` | Dense dimension the engine serves. Locked into the Qdrant collection; a mismatch is silent cache corruption, so startup logs a warning when `/models` disagrees. |
| `INFERENCE_EMBED_MODEL` | *(empty)* | Optional explicit model id for the `embed` role. |
| `INFERENCE_RERANK_MODEL` | *(empty)* | Optional explicit model id for the `rerank` role. |
| `INFERENCE_RELEVANCE_MODEL` | *(empty)* | Optional explicit model id for the `relevance` role. |
| `INFERENCE_NLI_MODEL` | *(empty)* | Optional explicit model id for the `nli` role. |

Behaviour:

- **Roles, not just models.** Requests are routed by role (`embed`, `rerank`,
  `relevance`, `nli`). This matters because two different cross-encoders back
  `rerank` and `relevance`; an unpinned request would otherwise be served by the
  engine's default reranker and quietly score relevance against the wrong model.
  An explicit `INFERENCE_*_MODEL` pin takes precedence and is what the Infinity
  deployment requires, since it routes by model id only.
- **Fail-open.** An unreachable engine is treated as a cache miss: the request is
  served by the provider and reported as `cache_bypassed`. The degradation log
  reason is `inference_unavailable`. Semantic caching is never allowed to fail a
  request.
- **Fail-fast.** A refused connection fails immediately (sub-millisecond to a few
  ms) instead of spending the latency budget on retry backoff; only timeouts,
  throttling, and 5xx are retried.
- **Sparse BM25 stays local.** `fastembed`'s sparse vectors are tokenizer-coupled
  to what is already stored in Qdrant, and the computation costs ~1ms.
- **Score scale is preserved.** The contract returns normalized scores, but the
  gateway consumes `score_raw` / `logits` because existing thresholds were
  calibrated against raw cross-encoder logits. Measured parity against the
  in-process models is at float precision (max abs diff ~5e-7).

## Semantic search tuning

| Variable | Default | Purpose |
| --- | --- | --- |
| `QDRANT_DENSE_SEARCH_HIGH_THRESHOLD` | `0.98` | Dense score at/above which the fast path serves immediately. |
| `QDRANT_SPARSE_SEARCH_HIGH_THRESHOLD` | `0.33` | Minimum RRF score for a candidate to be reranked. |
| `QDRANT_SEARCH_LIMIT` | `10` | Candidate pool size for the RRF query. |
| `DEFAULT_CACHE_TTL_SECONDS` | `86400` | Fallback TTL when a specific TTL is unavailable. |
| `CACHE_CONFIDENCE_THRESHOLDS` | see below | JSON map of intent → `{high, moderate}` acceptance thresholds. |
| `INTENT_TTL_SECONDS` | see below | JSON map of intent → TTL seconds. |

Default `CACHE_CONFIDENCE_THRESHOLDS` (`high` / `moderate`):

```text
code_generation 0.95/0.88 · data_extraction 0.96/0.90 · summarization 0.95/0.88
general_query 0.95/0.88 · code_explanation 0.94/0.85 · debugging 0.95/0.88
error_analysis 0.95/0.88 · refactoring 0.94/0.85 · optimization 0.94/0.85
test_generation 0.94/0.85 · chitchat 0.85/0.75 · greeting 0.85/0.75
weather 0.95/0.88 · finance_market 0.98/0.92 · product_info 0.95/0.88
factual_static 0.95/0.88
```

Default `INTENT_TTL_SECONDS`: `weather` 300 · `product_info` 3600 ·
`code_generation`/`code_explanation`/`debugging`/`error_analysis`/`refactoring`/
`optimization`/`test_generation`/`data_extraction`/`summarization`/`chitchat`
86400 · `greeting` 604800 · `factual_static` 2592000 · `general_query` 604800.

`time_sensitivity >= 0.9` or intent `finance_market` is never cached.

## Models and ML services

| Variable | Default | Purpose |
| --- | --- | --- |
| `EMBEDDING_MODEL_NAME` | `all-MiniLM-L6-v2` | Dense embedding model (384-dim). |
| `EMBEDDING_VERSION` | `v1` | Version tag for dense embeddings in Qdrant; bump to partition vector space when switching embedding models without dropped indices. |
| `SPARSE_EMBEDDING_VERSION` | `v1` | Version tag for sparse BM25 embeddings in Qdrant; bump when BM25 tokenizer or corpus changes. |
| `CACHE_KEY_VERSION` | `v1` | Version tag for Redis and Qdrant cache keys; bump to invalidate or segregate cache entries globally. |
| `PROMPT_VERSION` | `v1` | Prompt structure version tag stored in cache payloads and checked in exact/semantic lookups. |
| `INTENT_PROMPT_VERSION` | `v1` | Intent classification prompt version tag; bump to invalidate cached classifications. |
| `INTENT_CLASSIFIER_VERSION` | `v1` | Intent classifier model/rubric version tag. |
| `CORE_QUERY_EXTRACTOR_VERSION` | `v1` | Core query extractor prompt and model version tag. |
| `RERANKER_VERSION` | `v1` | Reranker scoring logic and model version tag. |
| `JUDGE_PROMPT_VERSION` | `v1` | Equivalence judge prompt template version tag for verifier and shadow cache. |
| `RERANKER_MODEL_NAME` | `BAAI/bge-reranker-base` | Declared reranker model name. |
| `RERANKER_SCORING_MODE` | `multiplicative` | How reranker signals combine: `multiplicative` (bounded factors multiplied into the cross-encoder score) or `additive` (symmetric bounded deltas). |
| `CROSS_ENCODER_RELEVANCE_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Relevance cross-encoder. |
| `CROSS_ENCODER_NLI_MODEL` | `cross-encoder/nli-deberta-v3-small` | NLI cross-encoder for contradiction/entailment. |
| `USE_RERANKER_MODEL` | `true` | Load the reranker. |
| `USE_RELEVANCE_MODEL` | `true` | Load the relevance model. |
| `USE_NLI_MODEL` | `true` | Load the NLI model. |
| `HF_HOME` | `./.model_cache` | Hugging Face cache dir (read directly in services). |
| `BM25_MODEL_PATH` | `""` | Pins the extracted BM25 model directory. When set, fastembed loads from it and skips its cache logic entirely — this is how the container makes the model deterministic. |
| `FASTEMBED_CACHE_PATH` | `<HF_HOME>/fastembed` | fastembed's own cache dir for the BM25 artifact (read directly in services). Distinct from `HF_HOME`: fastembed downloads two things into two caches, and only `cache_dir` holds the model it actually reads. |
| `BM25_LOCAL_FILES_ONLY` | `false` | When `true`, never download BM25 at runtime (read directly in services; the container sets this, since the model is baked in). |

In-process dense/cross-encoder models are an optional install:

```bash
poetry install --extras local-models   # torch, transformers, sentence-transformers
```

Without it, the gateway serves those roles from the inference platform
(`INFERENCE_BASE_URL`) and raises an explicit error if it is asked to do
otherwise. The published image does not include the extra: it takes the image
from 0.71GB to 9.4GB, and ~5.2GB of that is CUDA libraries (`nvidia` 3.3GB,
`torch` 0.9GB, `triton` 0.8GB) that these CPU-sized models never use.

### Intent classifier

| Variable | Default |
| --- | --- |
| `INTENT_CLASSIFIER_SERVICE` | `ollama` |
| `INTENT_CLASSIFIER_MODEL` | `qwen2.5-coder:3b` |
| `INTENT_CLASSIFIER_TEMPERATURE` | `0.0` |
| `INTENT_CLASSIFIER_TIMEOUT_SECONDS` | `2.5` |
| `INTENT_CLASSIFIER_MAX_TOKENS` | `500` |
| `INTENT_CLASSIFIER_REASONING_EFFORT` | `low` |
| `INTENT_CLASSIFIER_MAX_RETRIES` | `0` |

### Core query extractor and judge

| Variable | Default |
| --- | --- |
| `CORE_QUERY_EXTRACTOR_SERVICE` | `ollama` |
| `CORE_QUERY_EXTRACTOR_MODEL` | `qwen2.5-coder:1.5b` |
| `CORE_QUERY_EXTRACTOR_TEMPERATURE` | `0.0` |
| `CORE_QUERY_EXTRACTOR_MAX_TOKENS` | `200` |
| `EVALUATION_BATCH_SIZE` | `50` |
| `LLM_JUDGE_SERVICE` | `google-genai` |
| `LLM_JUDGE_MODEL` | `gemini-3.5-flash-lite` |
| `LLM_JUDGE_TEMPERATURE` | `0.0` |
| `LLM_JUDGE_MAX_TOKENS` | `200` |

## Observability & Telemetry

| Variable | Default | Purpose |
| --- | --- | --- |
| `OTEL_ENABLED` | `true` | Enable OpenTelemetry tracing instrumentation. When no exporter is configured, acts as zero-overhead NoOp. |
| `OTEL_SERVICE_NAME` | `echo-gate` | Service name attached to emitted traces and spans. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `""` | Target OTLP HTTP/gRPC collector (e.g., Langfuse `/api/public/otel`, Datadog, or Jaeger). |

## Not read by anything

`MIGRATION_DATABASE_URL` is consumed by Alembic's `env.py`/`alembic.ini`, not by
the application.

`api/app/.env.example` lists only variables the code reads; the earlier drift
(`EMBEDDING_DIMENSION`, `EMBEDDING_DEVICE`, and `CROSS_ENCODER_VERIFIER_MODEL`
being listed but unread, and `CROSS_ENCODER_RELEVANCE_MODEL` being read but
missing from the example) has been resolved.
