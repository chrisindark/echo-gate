# Changelog

All notable changes to Echo Gate are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The public API surface is pinned by the OpenAPI snapshot contract test
(`tests/test_openapi_snapshot.py`); breaking API changes are called out in the
changes' entry with a `[BREAKING]` marker.

## [Unreleased]

### Added

- **Shared inference platform (`INFERENCE_BASE_URL`)**: dense embeddings,
  reranking, relevance, and NLI are served over HTTP by `inference-platform/`
  instead of being loaded into every gateway process. One contract
  (`inference-platform/contract/INFERENCE_CONTRACT.md`) with two
  implementations: a native FastAPI server for laptops, and Infinity for GPU
  hosts. Requests route by **role** (`embed` / `rerank` / `relevance` / `nli`)
  so the two distinct cross-encoders cannot be confused for one another.
- **Strict startup verification**: `InferenceClient.verify()` runs at the end of
  startup and refuses to boot when no engine URL is set, an endpoint is
  unreachable, a required role is not loaded, or the engine's embedding
  dimension / model id disagrees with `INFERENCE_EMBEDDING_DIM` /
  `EMBEDDING_MODEL_NAME`. Previously a missing engine left the gateway serving
  every request with no cache while `/ready` stayed green.
- **`api/app/modules/inference/`**: `InferenceClient` — connection-pooled
  `httpx` client with role routing, retries for transient faults, and error
  classification (`InferenceUnavailable` vs `InferenceUnreachable` vs
  `InferenceRequestError` vs `InferenceConfigError`).
- **`inference-platform/`**: models lockfile with Hugging Face revisions,
  a drift-guard eval set, a latency bench, and a compose file for the Infinity
  deployment.

### Changed

- **The in-process model path is gone, not just optional.** `EmbeddingService`
  and `CrossEncoderService` hold only an `InferenceClient`; the Ollama embedding
  mode, the lazy `sentence-transformers` imports, and the `local-models` extra
  were deleted along with `--build-arg INSTALL_LOCAL_MODELS`. `poetry.lock` loses
  ~1,270 lines. `USE_RERANKER_MODEL` / `USE_RELEVANCE_MODEL` / `USE_NLI_MODEL`
  survive as per-role toggles (a disabled role raises instead of calling the
  engine).
- **A dead engine now pages instead of hiding**: `check_inference` is part of
  `GET /api/v1/health/deps` (`"inference": "failed"`, overall `degraded`) while
  `/ready` still gates on Postgres alone, since the gateway can still answer
  from the provider.
- **Model calls across the HTTP boundary are fail-open at runtime**: an
  unreachable inference engine degrades the request to the provider and reports
  `cache_bypassed` (log reason `inference_unavailable`) instead of raising. A
  refused connection fails fast in ~1-3ms rather than spending the latency
  budget on retry backoff.
- **`JudgeService` awaits the cross-encoder async variants** instead of blocking
  the event loop.
- Measured parity with the previous in-process models is float-exact (max abs
  diff ~5e-7 for rerank, relevance, and NLI logits; cosine 1.0 for embeddings).
- `scripts/calibrate_reranker.py` scores through `CrossEncoderService` against
  the engine, so calibration and production use the same model and request path.

- **Gateway image diet**: `sentence-transformers`, `transformers`, and `torch`
  are no longer dependencies, and the Dockerfile is multi-stage, so poetry and
  its wheel cache stay out of the runtime image. Measured: **0.71GB** vs
  **9.4GB** for the pre-removal build with in-process models (CUDA libraries
  alone were 3.3GB of the ~5.2GB it added) vs 1.04GB for the single-stage
  predecessor. Bytecode is precompiled, which costs ~0.1GB and halves cold import
  (2.4s → 1.17s).
  The image now also sets `PROJECT_NAME`, which `FastAPI` requires at import
  time, and pins `BM25_MODEL_PATH` so sparse BM25 resolves deterministically
  offline.
- `FASTEMBED_CACHE_PATH` and `BM25_LOCAL_FILES_ONLY` for controlling where the
  BM25 ONNX artifact lives and whether runtime may download it.

### Fixed

- **BM25 could not be pre-baked into a container.** `fastembed` keeps two caches:
  `cache_folder` (Hugging Face) and `cache_dir` (the extracted model the library
  actually reads). `EmbeddingService` passed only the former, so the artifact
  landed in a temp directory that is empty on every container start — the first
  image built this way failed offline. `cache_dir` is now explicit
  (`FASTEMBED_CACHE_PATH`, defaulting to `<HF_HOME>/fastembed`), the image stages
  the model and pins `BM25_MODEL_PATH`, and a test covers the regression.
- The pre-existing `Dockerfile` could never build: it pinned `poetry==1.8.3`
  while `poetry.lock` is `lock-version = "2.1"`, which Poetry 1.x cannot read.
- **Relevance scoring could silently use the reranker model** when the
  inference request named no model: both roles share `/rerank`, so the engine
  fell back to its default. Role routing fixes this and a regression test
  covers it.

- **Shadow cache pipeline (`CACHE_MODE`)**: new `disabled | shadow | active`
  mode. `shadow` answers every request from the provider and replays the full
  cache pipeline out of band — Redis exact → Qdrant exact → dense fast path →
  hybrid RRF + intent + cross-encoder rerank → confidence gate — recording what
  would have happened and warming the Redis/Qdrant corpus for activation.
  When `CACHE_MODE` is unset it is derived from the legacy `USE_*` flags, so
  existing deployments keep their behaviour.
- **`shadow_cache_evaluations` table** (Alembic `d4e5f6a7b8c9`, tenant RLS):
  would-be hit, match type, score inputs, both response texts, judge
  equivalence, intent, entities, latency breakdown, and projected latency
  saving per evaluation.
- **Shadow read API**: `GET /api/v1/shadow-cache/summary` (readiness status with
  reasons, hit/equivalence rates, match-type and per-model breakdowns) and
  `GET /api/v1/shadow-cache/evaluations` (paginated drill-down). Additive to the
  OpenAPI contract.
- **Judge equivalence check**: `JudgeService.evaluate_equivalence` compares a
  matched cached answer with the fresh one and returns
  `True`/`False`/`None`.
- **Analytics UI**: "Cache Readiness — Shadow Pipeline" section with would-be hit
  rate, judge equivalence, projected latency savings, match-type breakdown,
  readiness reasons, and per-model hit rates.
- **Configuration**: `SHADOW_SAMPLE_RATE`, `SHADOW_JUDGE_ENABLED`,
  `SHADOW_READINESS_MIN_EVALUATIONS`, `SHADOW_READINESS_MIN_HIT_RATE`,
  `SHADOW_READINESS_MIN_EQUIVALENCE_RATE`.

## [0.1.0] - 2026-10-21

Pre-release snapshot of the feature set; API surface locked via
`tests/snapshots/openapi.json`.

### Added

- **Gateway core**: OpenAI-compatible `POST /api/v1/chat/completions` with
  provider routing across Gemini (`google-genai`), OpenAI, Groq, OpenRouter,
  Ollama, Claude, and a mock provider; retry and primary→fallback provider
  switchover on both blocking and streaming paths; tool-call passthrough.
- **Streaming**: SSE responses with OpenAI-style chunks across providers,
  guarded in-memory accumulation, fallback switchover before/after the first
  chunk, cache-hit SSE playback, and background cache writes only on clean
  completion.
- **Multi-level semantic cache pipeline**: Redis exact → Qdrant exact →
  Qdrant semantic (dense fast-path, else hybrid dense/RRF fusion → intent
  classification → cross-encoder rerank → confidence gate); tenant/model/
  provider-sensitive keying; privacy-aware cache-scope widening; time-
  sensitive and finance-market prompts are never cached.
- **Multi-tenancy**: Postgres RLS with a non-owner app role, transaction-local
  tenant/user/system contexts, fail-closed defaults; `X-API-Key` as the sole
  identity credential; scoped `X-Admin-Key` admin surface with keys minted
  only by `app/scripts/create_admin_key.py`.
- **Quotas and rate limiting**: per-caller Redis RPM/TPM counters, monetary
  budget checks, rule-driven limits via admin CRUD, and hashed per-caller
  anonymous isolation.
- **Conversations**: mounted `/api/v1/conversations` routes (create, list,
  detail, chat-with-history) over RLS-scoped tables.
- **Background jobs**: Celery worker + beat (usage-cost backfill, pending-
  request evaluation, expired vector cleanup); background cache writes and
  trace/usage logging in-request.
- **Observability**: health/liveness/readiness/worker probes, non-gating
  `/health/deps`, structured logs with correlation IDs and per-stage latency
  fields, `X-Cache` header, traces and analytics endpoints,
  OpenAPI snapshot contract test.
- **UI** (`ui/`): chat playground, traces table + latency-waterfall detail,
  analytics dashboard, admin quota management, configurable backend URL.
- **Docs**: deep-reference tree in `docs/` and `docs/ROADMAP.md` as the plan
  of record.
- **Licensing**: AGPL-3.0; see `LICENSE` and `NOTICE`.
