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
  reranking, relevance, and NLI can be served over HTTP by `inference-platform/`
  instead of being loaded into every gateway process. One contract
  (`inference-platform/contract/INFERENCE_CONTRACT.md`) with two
  implementations: a native FastAPI server for laptops, and Infinity for GPU
  hosts. Requests route by **role** (`embed` / `rerank` / `relevance` / `nli`)
  so the two distinct cross-encoders cannot be confused for one another.
  Leaving `INFERENCE_BASE_URL` empty preserves the previous in-process
  behaviour exactly.
- **`api/app/modules/inference/`**: `InferenceClient` — connection-pooled
  `httpx` client with role routing, retries for transient faults, and error
  classification (`InferenceUnavailable` vs `InferenceUnreachable` vs
  `InferenceRequestError`).
- **`inference-platform/`**: models lockfile with Hugging Face revisions,
  a drift-guard eval set, a latency bench, and a compose file for the Infinity
  deployment.

### Changed

- **Model calls across the HTTP boundary are fail-open**: an unreachable
  inference engine now degrades the request to the provider and reports
  `cache_bypassed` (log reason `inference_unavailable`) instead of raising. A
  refused connection fails fast in ~1-3ms rather than spending the latency
  budget on retry backoff.
- **`CrossEncoderService` and `EmbeddingService` import torch lazily**, so a
  deployment running against the inference platform never loads it
  (verified: `torch`, `transformers`, and `sentence_transformers` are absent
  from `sys.modules`). `JudgeService` now awaits the cross-encoder async
  variants instead of blocking the event loop.
- Measured parity with the in-process models is float-exact (max abs diff
  ~5e-7 for rerank, relevance, and NLI logits; cosine 1.0 for embeddings).

### Fixed

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
