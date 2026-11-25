# Deploying Echo Gate as an LLM-only API (Supabase + Render free tiers)

This plan deploys **only the LLM-calling surfaces** of Echo Gate:

- `POST /api/v1/chat/completions` (OpenAI-compatible, streaming and non-streaming)
- `POST /api/v1/messages` and `POST /v1/messages` (Anthropic-compatible)
- `GET /v1/models` and `GET /api/v1/models`
- health probes (`/live`, `/ready`, `/health/deps`, `/api/v1/health`)

Everything else is off by environment variable: Redis cache, Qdrant, semantic
cache, intent classification, reranking, judging, shadow cache, cache audit,
Celery workers, the inference platform, the web UI, and the non-LLM admin APIs.

No Qdrant, no Redis, no inference engine, no Celery, and no `web/` deployment is
required.

---

## 1. Decision: keep Supabase Postgres

**Recommended profile: Supabase Postgres + Render Docker web service, with
`CACHE_MODE=disabled`.**

Why keep Postgres:

- It is already the auth store. API keys are SHA-256 hashes in `api_keys`, and
  `TenantContextMiddleware` resolves tenant/user identity from them.
- It backs quota rules and usage/gateway logs, which already exist and are
  fail-open on write.
- RLS, migrations, and the `echo_gate_app` role are already implemented.
- A fully stateless deployment would need new code for a static env API key,
  quota bypass, usage-log bypass, and DB-free readiness. That is more code, a
  single shared credential, and no revocation path. It is not recommended.

The cost is one managed Postgres. Supabase's free tier is sufficient: the app
uses a handful of small tables and a 2-connection pool.

### What is intentionally not deployed

| Component                                                                   | Why                                | Controlled by              |
| --------------------------------------------------------------------------- | ---------------------------------- | -------------------------- |
| Redis cache                                                                 | No cache in this profile           | `CACHE_MODE=disabled`      |
| Qdrant                                                                      | No vector cache                    | `CACHE_MODE=disabled`      |
| Inference platform                                                          | No embeddings/rerank/NLI           | `CACHE_MODE=disabled`      |
| Intent classifier / reranker / judge                                        | Cache-only paths                   | `CACHE_MODE=disabled`      |
| Shadow cache / cache audit                                                  | Cache-only paths                   | `CACHE_MODE=disabled`      |
| Celery workers                                                              | No background jobs in this profile | no worker service deployed |
| Web UI                                                                      | API-only release                   | do not deploy `web/`       |
| Admin, analytics, usage, quota, verifier, gateway, conversation, cache APIs | Not LLM-calling surfaces           | `ENABLED_SURFACES`         |

---

## 2. Required code changes

The current process refuses to start without an inference engine, even when the
cache is disabled. `DependencyContainer.initialize()` always constructs
`EmbeddingService` (which raises `InferenceConfigError` when
`INFERENCE_BASE_URL` is unset) and always calls `InferenceClient.verify()`
(which also refuses to start). Those are the only hard blockers.

The changes below are the smallest set that makes the deployment fully
environment-controlled. Defaults preserve today's behavior, so the existing
OpenAPI snapshot and full-stack deployments are unaffected.

### 2.1 `api/app/core/config.py`

Add two settings and one helper.

```python
# after CACHE_MODE parsing
self.ALLOW_ANONYMOUS = _bool_env("ALLOW_ANONYMOUS", True)

raw_surfaces = os.getenv("ENABLED_SURFACES", "all")
self.ENABLED_SURFACES = {
    part.strip().lower() for part in raw_surfaces.split(",") if part.strip()
} or {"all"}
```

```python
@property
def cache_enabled(self) -> bool:
    """True when any cache layer may be constructed or read."""
    return self.CACHE_MODE != "disabled"

def surface_enabled(self, name: str) -> bool:
    """Whether an API surface is mounted. `all` keeps the full-stack default."""
    return "all" in self.ENABLED_SURFACES or name in self.ENABLED_SURFACES
```

`ALLOW_ANONYMOUS` defaults to `true` so local development and existing tests are
unchanged. The production profile sets it to `false`: a public LLM proxy must not
spend provider credit for keyless callers.

### 2.2 `api/app/core/dependencies.py`

Make Redis ping optional and skip every cache-only service when the cache is
disabled. Redis itself is still constructed because the quota service binds to
it; without a server, quota counters already fail open.

```python
@classmethod
async def initialize(cls) -> None:
    cache_enabled = config.cache_enabled

    # Redis is always constructed: LlmQuotaService binds to it. Only ping when
    # the cache is enabled; with no server, quota operations fail open.
    await cls.get_redis_service(ping=cache_enabled)

    if cache_enabled:
        cls.get_embedding_service()
        cls.get_qdrant_client_service()
        cache_service = cls.get_llm_cache_collection_service()
        await cache_service.ensure_indexes_async()
        cls.get_reranker_service()
        await cls.get_intent_classifier_service()
        cls.get_entity_extractor_service()
        cls.get_cross_encoder_service()
        cls.get_instruction_service()
        cls.get_judge_service()
        cls.get_shadow_cache_service()

    cls.get_llm_provider_service()
    await cls.get_llm_router_service()
    cls.get_llm_usage_service()
    cls.get_llm_quota_service()
    cls.get_gateway_requests_service()

    if cache_enabled:
        engine = await cls.get_inference_client().verify()
        logger.info(
            "inference engine verified (roles=%s, device=%s)",
            sorted(engine.get("loaded") or {}),
            engine.get("device"),
        )
```

```python
@classmethod
async def get_redis_service(cls, *, ping: bool = True) -> RedisService:
    if cls._redis_service is None:
        cls._redis_service = RedisService()
    if ping:
        await cls._redis_service.ping()
    return cls._redis_service
```

Update `get_llm_router_service()` to pass `None` for cache-only dependencies and
to avoid a second startup ping:

```python
@classmethod
async def get_llm_router_service(cls) -> LlmRouterService:
    if cls._llm_router_service is None:
        cache_enabled = config.cache_enabled
        cls._llm_router_service = LlmRouterService(
            llm_provider_service=cls.get_llm_provider_service(),
            embedding_service=cls.get_embedding_service() if cache_enabled else None,
            llm_cache_service=(
                cls.get_llm_cache_collection_service() if cache_enabled else None
            ),
            redis_service=await cls.get_redis_service(ping=False),
            reranker_service=cls.get_reranker_service() if cache_enabled else None,
            intent_classifier_service=(
                await cls.get_intent_classifier_service() if cache_enabled else None
            ),
            entity_extractor_service=(
                cls.get_entity_extractor_service() if cache_enabled else None
            ),
            llm_usage_service=cls.get_llm_usage_service(),
            llm_quota_service=cls.get_llm_quota_service(),
            gateway_requests_service=cls.get_gateway_requests_service(),
            shadow_cache_service=(
                cls.get_shadow_cache_service() if cache_enabled else None
            ),
            judge_service=cls.get_judge_service() if cache_enabled else None,
            cache_audit_service=(
                cls.get_cache_audit_service() if cache_enabled else None
            ),
            prompt_optimizer=cls.get_prompt_optimizer(),
        )
    return cls._llm_router_service
```

`shutdown()` already null-checks every service; no change is needed there.

### 2.3 `api/app/modules/llm/llm_router_service.py`

Mark the cache-only constructor arguments optional. `redis_service` stays
required because it is always constructed.

```python
def __init__(
    self,
    llm_provider_service: LlmProviderService,
    embedding_service: EmbeddingService | None,
    llm_cache_service: LlmCacheCollectionService | None,
    redis_service: RedisService,
    reranker_service: RerankerService | None,
    intent_classifier_service: IntentClassifierService | None,
    entity_extractor_service: EntityExtractorService | None,
    llm_usage_service: LlmUsageService,
    ...
) -> None:
```

Add one defensive guard so a disabled cache can never reach the embedding call:

```python
async def _get_embeddings(
    self, text: str, get_sparse: bool = True, prefix: str = ""
) -> tuple[list[float] | None, dict[str, list] | None]:
    if not text or self.embedding_service is None:
        return None, None
    ...
```

All other uses are already behind `if self.llm_cache_service` /
`if not self.llm_cache_service: return` guards, and `cache_layer_enabled()`
returns `False` for every layer when `CACHE_MODE != "active"`.

### 2.4 `api/app/modules/chat/chat_controller.py`

Split the operator/cache diagnostics out of the LLM router so the `chat` surface
can be mounted without them. Keep the existing name for the LLM routes.

```python
api_v1_router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])
cache_admin_router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])
```

Change the three diagnostics (`/completions/semantic-search`,
`/completions/exact-search`, `/completions/cache-save`) to
`@cache_admin_router.post(...)`. `/completions` and `/estimate` stay on
`api_v1_router`.

### 2.5 `api/app/main.py`

Replace unconditional router registration with surface gating.

```python
from app.modules.chat.chat_controller import (
    api_v1_router as chat_router,
    cache_admin_router as chat_cache_admin_router,
)


def _include(surface: str, router) -> None:
    if config.surface_enabled(surface):
        app.include_router(router)


_include("health", root_health_router)
_include("health", health_router)
_include("chat", chat_router)
_include("cache_admin", chat_cache_admin_router)
_include("anthropic", anthropic_router)
_include("models", models_router)
_include("conversations", conversation_router)
_include("cache_admin", qdrant_router)
_include("usage", llm_usage_router)
_include("verifier", verifier_router)
_include("gateway", gateway_requests_router)
_include("analytics", analytics_router)
_include("shadow", shadow_cache_router)
_include("api_keys", api_keys_router)
_include("quota", llm_quota_router)
_include("cache_admin", cache_admin_router)
```

Keep the registration order identical to today. With `ENABLED_SURFACES=all`
(the default), the OpenAPI contract is byte-for-byte unchanged.

Surface names: `health`, `models`, `chat`, `anthropic`, `conversations`,
`usage`, `verifier`, `gateway`, `analytics`, `shadow`, `api_keys`, `quota`,
`cache_admin`.

### 2.6 `api/app/core/guards.py`

Honor the anonymous switch at request time (guards are module-level singletons,
so this cannot be decided at import).

```python
from app.core.config import config

...
        if not api_key:
            if self._allow_anonymous and config.ALLOW_ANONYMOUS:
                return True
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "API Key missing. Provide a valid "
                    f"{EchoGateHeaders.API_KEY} header or Authorization: Bearer token."
                ),
            )
```

### 2.7 `Dockerfile`

Bind the platform-provided port. Render and Railway inject `PORT`.

```dockerfile
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

### 2.8 `api/app/.env.example`

Add the two new variables and document the minimal profile so the next operator
does not have to rediscover it:

```dotenv
# API surfaces to mount. `all` preserves the full-stack surface.
# Minimal LLM-only deployment: health,models,chat,anthropic
ENABLED_SURFACES=all

# Require a valid API key on chat and model routes. Set false in production.
ALLOW_ANONYMOUS=true
```

### 2.9 Tests to add or update

- `config`: `ENABLED_SURFACES` parsing (`all`, explicit list, empty → `all`) and
  `surface_enabled()`.
- `LlmRouterService`: constructing it with `None` for the cache-only
  dependencies and calling `_get_embeddings()` returns `(None, None)`.
- Guards: with `ALLOW_ANONYMOUS=false`, a keyless chat request returns `401`;
  with a valid key it proceeds.
- OpenAPI snapshot: run unchanged with `ENABLED_SURFACES` unset.

Proof commands:

```bash
poetry run python -m compileall api/app
poetry run pytest tests/contract/test_openapi_snapshot.py
poetry run pytest api/app/core/tests api/app/modules/llm/tests
```

---

## 3. Supabase setup

### 3.1 Create the project

1. Create a Supabase project (free plan).
2. Save the database password.
3. Copy the **Session pooler** connection string from **Connect**. It looks like:

   ```text
   postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-[INDEX]-[REGION].pooler.supabase.com:5432/postgres
   ```

   Session mode is required: the app uses `set_config(..., is_local => true)`
   per transaction, and session mode supports prepared statements. Transaction
   mode (port `6543`) would require disabling prepared statements and loses
   session state.

### 3.2 Run the migrations from your machine

Render's free tier has no shell and no one-off jobs, so run Alembic locally.
Migrations must run as the table owner, not the app role.

```bash
export MIGRATION_DATABASE_URL='postgresql+psycopg://postgres.[PROJECT-REF]:[PASSWORD]@aws-[INDEX]-[REGION].pooler.supabase.com:5432/postgres?sslmode=require'
export DB_APP_ROLE=echo_gate_app
export DB_APP_PASSWORD='<strong-app-password>'

poetry run alembic upgrade head
```

The baseline migration (`3f9c1a7b2d40`) creates every table, enables RLS, and
creates the non-owner `echo_gate_app` role with the password from
`DB_APP_PASSWORD`.

If `CREATE ROLE` is denied, create the role in the Supabase dashboard
(**Database → Roles → Add role**, login enabled, same password) and re-run the
migration; the grants and policies still apply.

### 3.3 Create the application API key

The app role cannot read a plaintext key, so generate one and insert only its
SHA-256 hash. The key is shown once.

```bash
KEY="eg_$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo "API key: $KEY"
printf %s "$KEY" | shasum -a 256
```

Then, in the Supabase SQL editor (runs as the owner; `api_keys` has no RLS by
design):

```sql
insert into api_keys
  (name, key_prefix, key_hash, tenant_id, user_id, is_active, created_at, updated_at)
values
  ('render-prod',
   'eg_XXXXXXXX',          -- first 11 characters of the key
   '<sha256-hex-digest>',  -- output of shasum -a 256
   'tenant-prod',
   null,
   true,
   now(),
   now());
```

Use the plaintext key as `X-API-Key: <key>` or `Authorization: Bearer <key>`.

### 3.4 Supabase free-tier notes

- Free projects pause after about 7 days without activity. The app touches the
  DB on every keyed request (API key resolution, quota rules, usage logs), so
  normal traffic keeps it awake. For a quiet deployment, schedule a daily
  request to `/ready` (it probes Postgres) from an external cron.
- A paused project makes keyed requests return `401` (key resolution fails
  closed) and makes `/ready` fail. `/live` stays green.
- The app pool must stay small: Supabase's free tier has a shared pooler limit,
  and Render gives the container 512 MB.
- `/ready` probes Postgres with a 1 s timeout. The first probe after a cold
  start or a pooler reconnect can exceed that and report `503`. Keep Render's
  health check on `/live` and treat a single `/ready` failure as retryable.

---

## 4. Render setup

### 4.1 Create the service

1. **New → Web Service**, connect the repository.
2. Runtime: **Docker**.
3. Dockerfile path: `./Dockerfile`.
4. Plan: **Free**.
5. Health check path: `/live` (does not touch the database).
6. Do **not** add a disk, a cron job, a worker, or a Redis/Qdrant service.

Render injects `PORT`; the Dockerfile change in §2.7 binds it.

### 4.2 Environment variables (exact)

Set these in the Render dashboard. Never commit them.

```dotenv
# --- runtime ---------------------------------------------------------------
PYTHON_ENV=production
PROJECT_NAME=echo-gate
VERSION=0.4.0

# --- minimal profile: no cache, no inference, no optional infrastructure ----
CACHE_MODE=disabled
ENABLED_SURFACES=health,models,chat,anthropic
ALLOW_ANONYMOUS=false

USE_REDIS_EXACT_MATCHING=false
USE_QDRANT_EXACT_MATCHING=false
USE_QDRANT_SEMANTIC_MATCHING=false

# Leave the inference engine unset. It must not be reachable in this profile.
INFERENCE_BASE_URL=

OTEL_ENABLED=false
EVENTS_ENABLED=false
TELEMETRY_ENABLED=false
PROMPT_OPTIMIZATION_ENABLED=false
CACHE_AUDIT_ENABLED=false

# --- Postgres: Supabase session pooler --------------------------------------
# Use the app role, not `postgres`. Percent-encode reserved characters.
DATABASE_URL=postgresql+psycopg://echo_gate_app.[PROJECT-REF]:[APP-PASSWORD]@aws-[INDEX]-[REGION].pooler.supabase.com:5432/postgres?sslmode=require
DATABASE_URL_READ=postgresql+psycopg://echo_gate_app.[PROJECT-REF]:[APP-PASSWORD]@aws-[INDEX]-[REGION].pooler.supabase.com:5432/postgres?sslmode=require
DB_POOL_SIZE=2
DB_MAX_OVERFLOW=2
DB_POOL_TIMEOUT=10
DB_POOL_RECYCLE=300
DB_POOL_PRE_PING=true

# --- provider credentials ---------------------------------------------------
# At least one provider must be configured. Set only the ones you use.
OPENAI_API_KEY=
GEMINI_API_KEY=
GROQ_API_KEY=
OPENROUTER_API_KEY=
CLAUDE_API_KEY=

# Default and fallback model. `google-genai` is the config default.
DEFAULT_LLM_SERVICE=google-genai
USE_FALLBACK_LLM=true
FALLBACK_LLM_SERVICE=google-genai
FALLBACK_LLM_MODEL=gemini-3.1-flash-lite

# --- client behavior --------------------------------------------------------
LLM_PROVIDER_TIMEOUT_SECONDS=15.0
HTTP_CLIENT_TIMEOUT_SECONDS=120.0
LLM_MAX_RETRIES=0

# Browser origins. Leave the default for API-only, or set your frontend origin.
CORS_ORIGINS=https://your-frontend.example
```

Do **not** set `MIGRATION_DATABASE_URL` on Render. It is only for local Alembic
runs and would give the running app owner credentials if used accidentally.

Do **not** set `REDIS_URL`, `QDRANT_URL`, or any `INFERENCE_*` URL.

### 4.3 Optional: rate limiting without the cache

With no Redis, `LlmQuotaService` fails open: RPM/TPM counters are not enforced.
Authentication still prevents keyless abuse, and keys are revocable.

If you want the existing quota counters back without enabling the cache, point
`REDIS_URL` at a serverless Redis free tier (for example Upstash) and leave
`CACHE_MODE=disabled`. The cache stays off; only quota counters use Redis. This
is env-only and needs no code change.

### 4.4 Render free-tier behavior

- Spins down after 15 minutes idle; the first request after that pays a cold
  start (roughly 30–60 s).
- 750 instance-hours/month, 0.1 CPU, 512 MB RAM.
- Ephemeral filesystem: no local state is needed.
- No shell and no one-off jobs: migrations run from your machine (§3.2).
- Free services get a `*.onrender.com` hostname.

---

## 5. Railway alternative

Railway works with the same image and env. Differences:

- Free plan: $1/month usage credit, 1 project, 3 services, 0.5 GB RAM.
- Services sleep after 5–10 minutes; cold starts are similar.
- Set the start command through the Dockerfile (already fixed by §2.7) and the
  health check path to `/live`.
- Keep the same `DATABASE_URL`/`DATABASE_URL_READ` (Supabase session pooler) and
  the same `ENABLED_SURFACES`.

Railway is a fallback, not the recommendation: the $1 credit is easy to exhaust
and the sleep window is more aggressive.

---

## 6. Deployment sequence

1. Land the code changes from §2 and run the proof commands.
2. Create the Supabase project.
3. Run `alembic upgrade head` against Supabase.
4. Insert the production API key hash.
5. Create the Render Docker web service.
6. Set every env var from §4.2, with a real provider key and the Supabase app
   role URL.
7. Deploy.
8. Wait for the first boot, then run the verification in §7.
9. Point clients at `https://<service>.onrender.com`.

Order matters: migrate before the first request. The process starts without
touching Postgres (`/live` is green), but `/ready` and keyed chat need the schema
and the app role.

---

## 7. Verification

### 7.1 Health

```bash
BASE=https://<service>.onrender.com

curl -fsS "$BASE/live"
curl -fsS "$BASE/ready"
curl -fsS "$BASE/health/deps"
curl -fsS "$BASE/api/v1/health"
```

Expected:

- `/live` → `200`.
- `/ready` → `200` once Postgres and the app role are reachable.
- `/health/deps` → `200`; `database` healthy, `redis`/`qdrant` may be reported
  as unavailable without failing the response.
- `/api/v1/health` → `200`.

### 7.2 Models

```bash
curl -fsS "$BASE/v1/models" -H "X-API-Key: $KEY"
curl -fsS "$BASE/api/v1/models" -H "X-API-Key: $KEY"
```

Expected: only configured providers/models are listed.

### 7.3 Chat, non-streaming

```bash
curl -fsS "$BASE/api/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{
    "model": "gemini-3.1-flash-lite",
    "messages": [{"role": "user", "content": "Reply with the word: ok"}]
  }'
```

Expected: a normal OpenAI-shaped completion. `X-Cache` should be `MISS` or
absent; there is no cache in this profile.

### 7.4 Chat, streaming

```bash
curl -N "$BASE/api/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{
    "model": "gemini-3.1-flash-lite",
    "stream": true,
    "messages": [{"role": "user", "content": "Count to three."}]
  }'
```

Expected: `text/event-stream` chunks terminated by `[DONE]`.

### 7.5 Anthropic Messages

```bash
curl -fsS "$BASE/api/v1/messages" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-3-7-sonnet-20250219",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "Reply with: ok"}]
  }'
```

Expected: an Anthropic Messages response. Use a Claude key, or point the request
at a configured provider/model.

### 7.6 Security and surface checks

```bash
# Keyless chat must be rejected.
curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST "$BASE/api/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini-3.1-flash-lite","messages":[{"role":"user","content":"hi"}]}'
# expected: 401

# Disabled surfaces must be 404.
for path in /api/v1/admin/api-keys /api/v1/llm-usage /api/v1/analytics \
            /api/v1/gateway-requests /api/v1/chat/completions/semantic-search; do
  curl -s -o /dev/null -w "$path %{http_code}\n" "$BASE$path" -H "X-API-Key: $KEY"
done
# expected: 404 for each
```

### 7.7 Local smoke test without provider credit

Run the gateway locally with the same minimal env and the deterministic mock
provider:

```bash
CACHE_MODE=disabled \
ENABLED_SURFACES=health,models,chat,anthropic \
ALLOW_ANONYMOUS=true \
INFERENCE_BASE_URL= \
DEFAULT_LLM_SERVICE=mock \
ALLOWED_MODELS_JSON='{"mock":["mock"]}' \
DATABASE_URL='postgresql+psycopg://...' \
poetry run uvicorn app.main:app --port 8000
```

Then call `/api/v1/chat/completions` with `"service_name": "mock"` and
`"model": "mock"`. This proves startup, routing, and the chat path without any
cache, Redis, Qdrant, inference engine, or provider key.

---

## 8. Definition of done

- [ ] `CACHE_MODE=disabled` with `INFERENCE_BASE_URL` unset starts successfully.
- [ ] `ENABLED_SURFACES` mounts only `health`, `models`, `chat`, `anthropic`.
- [ ] Disabled surfaces return `404`.
- [ ] `ALLOW_ANONYMOUS=false` returns `401` for keyless chat.
- [ ] `/live` is green; `/ready` is green against Supabase.
- [ ] `/v1/models` lists only configured providers.
- [ ] Chat works blocking and streaming.
- [ ] Anthropic Messages works.
- [ ] No Redis, Qdrant, inference, Celery, or web service is deployed.
- [ ] OpenAPI snapshot is unchanged with default env.
- [ ] The production API key hash exists in `api_keys`.

---

## 9. Known limitations of this profile

- **No cache.** Every request reaches the provider. `X-Cache` is absent or
  `MISS`.
- **No semantic search, reranking, judging, shadow cache, or audit.** Those
  surfaces are not mounted.
- **No rate limiting unless Redis is added.** Quota counters fail open without
  Redis. Authentication and key revocation still apply.
- **Cold starts.** Render/Railway free tiers sleep; the first request after idle
  is slow.
- **Supabase free-project pausing.** After about 7 days without DB activity,
  keyed requests return `401` and `/ready` fails until the project is resumed.
  A daily `/ready` ping prevents this.
- **Single region, single replica.** No horizontal scaling or multi-region cache.
- **Migrations are manual.** Render free has no shell; run Alembic from a
  trusted machine.
