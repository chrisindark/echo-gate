# Changelog

All notable changes to Echo Gate are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The public API surface is pinned by the OpenAPI snapshot contract test
(`tests/test_openapi_snapshot.py`); breaking API changes are called out in the
changes' entry with a `[BREAKING]` marker.

## [Unreleased]

### Added

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
