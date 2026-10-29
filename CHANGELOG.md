# Changelog

All notable changes to Echo Gate are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The public API surface is pinned by the OpenAPI snapshot contract test
(`tests/test_openapi_snapshot.py`); breaking API changes are called out in the
changes' entry with a `[BREAKING]` marker.

## [Unreleased]

### Added

- **Pre-flight cost estimate endpoint**: `POST /api/v1/chat/estimate` counts
  prompt tokens with `tiktoken` (+4 per message, +3 reply priming, tool
  declarations included) and returns a cheapest-first cost matrix across every
  enabled model without calling a provider. A missing BPE encoding (offline
  engine) degrades to a 4-chars/token estimate that the response labels as
  `heuristic` instead of failing. `ESTIMATE_DEFAULT_COMPLETION_TOKENS` (256)
  supplies the completion length when `max_tokens` is omitted. Additive: new
  route and schemas only, OpenAPI snapshot regenerated.
- **Value-aware cache retention (vCache-style)**: cache entries now record
  `provider_cost`, `latency_ms`, `token_count`, `hit_count`, and a
  `retention_value` score, so intent TTLs stretch for slow, expensive answers
  (up to `CACHE_RETENTION_MAX_TTL_SECONDS`) and the eviction sweep deletes
  cheap expired entries first while protecting valuable ones for a grace period
  (`CACHE_RETENTION_PROTECT_THRESHOLD`) and evicting capacity overflow
  lowest-value first (`CACHE_RETENTION_MAX_POINTS`). Curves live in
  `app/modules/qdrant/retention_policy.py`; set `CACHE_RETENTION_ENABLED=false`
  to restore the flat TTL sweep.
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
- **`sparse` role on the shared contract**: `POST /embeddings` accepts `sparse:
  true` and returns BM25 `indices`/`values` for the same batch as the dense
  `data`, in one round trip — a hybrid consumer pays one hop, not two. `/health`
  reports every role's `kind` and, for sparse, the `fastembed` `backend` version,
  because the tokenizer build is part of the sparse vector space. A sparse
  failure returns the dense vectors plus `sparse_error` instead of failing the
  request. Echo Gate keeps BM25 local until its Qdrant collection is rebuilt; the
  role exists for doc-nexus and agent-forge.
- **`inference-platform/` is its own dependency root**: `pyproject.toml` +
  committed `uv.lock`, a `src/inference_platform/` package (`api`, `roles`,
  `schemas`, `settings`, `errors`), a console entry point, and a wire-contract
  test suite that runs without downloading weights. It replaces
  `dev/requirements.txt` and the single-file `dev/native_server.py`, so the
  directory can move to its own repository unchanged.
- **Zero-shot classification (`POST /classify`, `mode: "zero-shot"`)**: candidate
  labels become NLI hypotheses, so mail/document triage needs one small
  cross-encoder instead of a generative model. Each input returns independent
  per-label entailment probabilities (`scores`), a softmax across labels
  (`scores_normalized`) for single-choice ranking, and a pre-sorted `ranked` list.
  The label set, the `hypothesis_template`, and the pair budget are validated with
  structured `400`s. Documented honestly as a *ranking*: with noun labels the
  shipped NLI checkpoint separates topics well and intents poorly (the
  `hypotheses` entry below is the fix for that).
- **A dedicated `zero-shot` role, and a model set sized for the machine it runs
  on.** Zero-shot mode now prefers its own binary entailment checkpoint
  (`ModernBERT-base-zeroshot-v2.0`) and falls back to `nli` on engines that lack
  one; responses name the answering `role` and `model`. Every role's default was
  replaced with a checkpoint that is genuinely better at its job on this machine:
  `embed` `all-MiniLM-L6-v2` → `bge-small-en-v1.5` (paraphrase 0.62 → 0.85 at the
  same 384 dims, so the Qdrant schema survives), `rerank` `bge-reranker-base` →
  `gte-reranker-modernbert-base` (1.08 GB → 598 MB, and the only candidate that
  separates short query↔prompt pairs on the raw-logit scale the confidence gate
  uses), `nli` `nli-deberta-v3-small` → `nli-MiniLM2-L6-H768` (552 MB → 316 MB,
  same verdicts, 2× faster). The five dense/cross-encoder roles now total
  **1419 MB instead of 1809 MB**, and a live paraphrase moved from a 0.05
  composite rerank score (outside the calibrated band) to 0.91 (inside it). The
  production upgrade for every role is recorded as a comment in
  `inference-platform/models.lock.yaml` and as a release checklist in
  `docs/ROADMAP.md`.
- **The native server is deployable off the laptop**: a CPU-only `Dockerfile`
  (torch pinned to the PyTorch CPU index — the PyPI wheel drags the whole CUDA
  runtime into the image on Linux), `deploy/compose/inference-native.yml`, and a
  Helm chart in `deploy/helm/inference-platform` with readiness on `/health`, TCP
  liveness (so a permanently unloaded role is "unready", not a restart loop),
  optional model-cache PVC with a prefetch init container, and an
  `inference-platform-prefetch` command that fills a cache without serving.
- **`hypotheses` on `/classify`: verbatim assertions instead of one template.**
  Zero-shot's real weakness is the *question*, not the checkpoint: with noun
  labels the shipped model ranks a phishing email "account notice" at 0.983
  ("phishing" 0.016), because the message genuinely is notice-shaped. Callers can
  now send `hypotheses` (label → assertions, each scored by entailment, label
  scored by `max`) so the label stays the key in `scores`, and `best_hypothesis`
  names the phrasing that won. Measured on the shipped model over six texts:
  noun labels 5/6, abstract judgements 4/6 (scores collapse to ~0), behaviour
  claims 5/6 (two labels true at once), behaviour claims with mutually exclusive
  labels **6/6 with every margin ≥ 0.93**. `evals/check.py` gained a
  `zero-shot-intent` group pinning that, and the contract documents the recipe
  and the abstain threshold. Additive: `hypothesis_template` requests are
  byte-identical, and an engine that predates the field skips the new eval group.

### Changed

- **`inference-platform/` packaging**: `dev/requirements.txt` and
  `dev/native_server.py` are gone; the server is the installed
  `inference_platform` package and `dev/serve.sh` runs
  `uv run --frozen inference-platform`. `dev.sh`, `docs/DEVELOPMENT.md`, and
  `AGENTS.md` use `uv sync` / `uv run` instead of
  `python3.11 -m venv` + `pip install -r`.

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

- **Semantic cache writes and searches raised `AttributeError`.** Removing the
  in-process model path deleted `EmbeddingService.model_name`, but the router
  still read it to build the Qdrant `embedding_model` payload field and filter.
  It is now a property sourced from `INFERENCE_EMBED_MODEL` /
  `EMBEDDING_MODEL_NAME`, which `InferenceClient.verify()` already keeps in sync
  with the engine.
- **`scripts/migrate_embeddings.py` could not migrate anything.** It hardcoded
  `EMBEDDING_DIM = 768` (the engine emits 384) and aimed at a collection schema
  the gateway no longer writes (`intent_embedding` / `response_embedding`), then
  built a fresh `EmbeddingService` per text through a stale Ollama/nomic path. It
  is now a re-embed migration: it derives the dimension from the engine, creates
  the target through `LlmCacheCollectionService` so the schema cannot drift,
  batch-embeds each dense field, copies the BM25 sparse vectors unchanged (they
  are tokenizer-coupled, not model-coupled), and rewrites
  `embedding_model`/`embedding_version` on each payload. Dry-run by default.
- **The LLM judge could never run.** `judge_service.py` built its
  `ChatCompletionRequest` without `service_name`, so the schema validated
  `LLM_JUDGE_MODEL` (`gemini-3.5-flash-lite`) against the default `"ollama"`
  allow-list and raised `ValidationError` on every call. The shadow pipeline
  swallowed it, recorded `is_equivalent = None` for every evaluation, and could
  never establish equivalence. Both judge requests now pass
  `config.LLM_JUDGE_SERVICE`.
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
