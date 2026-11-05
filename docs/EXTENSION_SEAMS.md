# Extension Seams — open-core structure

> **Status:** Implemented seams (2026-11-05). The OSS repo stays genuinely
> self-hostable; cloud/enterprise features register behind these seams
> without forking the core. See ROADMAP.md §Phase 4 for the strategy.

## What "seam" means here

A seam is a stable, documented extension point with a local OSS default:

| Seam | File | OSS default | Cloud overlay registers |
| --- | --- | --- | --- |
| **Feature flags** | `api/app/core/features.py` | `mode() == "oss"`, no features | `register_cloud()` flips `billing`, `metering_sink`, `event_bus` |
| **Identity** | `api/app/core/identity.py` | `ApiKeyIdentityProvider` (X-API-Key, server-side) | `OidcIdentityProvider` + session portal, appended in `default_provider()` |
| **Metering** | `api/app/core/metering.py` | `NoopMeteringSink` (usage still lands in `llm_usage`) | Stripe recorder sink |
| **Event bus** | `api/app/core/events.py` | `WebhookEventSink` — per-type `EVENTS_WEBHOOK_*` URLs, dropped when unset | Stripe/PagerDuty/Slack fan-out sink via `set_event_sink()` |
| **Storage** | `api/app/core/storage.py` | Plain `RedisService()` / `QdrantClientService()` URLs | Managed-service clients (TLS, IAM auth, retry policy) |
| **Admin surface** | `api/app/main.py` | `/api/v1/admin/*` only | `billing:*` routes, mounted only when `features.is_enabled("billing")` |

Behavior in the OSS build is unchanged: these are wiring refactorings, not
feature additions. The OpenAPI snapshot gate confirms it — no routes or
schemas changed.

## How the cloud overlay registers (without forking core)

```python
# In the private echo-gate-cloud package (never in this repo):
from app.core import features, identity, metering, storage

features.register_cloud()                      # flips DEPLOYMENT_MODE to cloud
identity.default_provider = lambda: OidcIdentityProvider(...)  # plus session
metering.default_sink = lambda: StripeRecorderSink(...)
storage.default_factory = lambda: ManagedStorageFactory(...)
events.set_event_sink(PagerDutySlackFanout(...))  # replaces the webhook sink
```

## Event bus

`api/app/core/events.py` emits four typed events, all from background-task
paths so webhook I/O never touches client latency:

| Event | Emitted from | Meaning |
| --- | --- | --- |
| `cache.miss` | `LlmRouterService._log_usage` (already a background task) | A request missed the cache and a provider served it. |
| `quota.exceeded` | `LlmQuotaService.check_quota` refusal branches | RPM (`rate_limit`), TPM (`token_limit`), or budget (`budget_exhausted`) refused a caller. |
| `provider.failure` | `LlmRouterService._call_provider_with_retry` per failed attempt | An upstream attempt failed; `final: true` marks the failure the caller saw. |
| `audit.failed` | `LlmRouterService._background_cache_audit` | A sampled audit judged a served entry non-equivalent (`evicted` says whether the kill switch purged it), could not fetch ground truth, or raised. |

Dispatch prefers Celery (`app.core.events.deliver_event`, defined in
`api/app/core/tasks.py` and resolved lazily so `events.py` never imports the
Celery app at module load) and otherwise runs delivery on a task or daemon
thread, so webhook I/O is never inline on a request path.

Two layers of gating keep the OSS default inert: `EVENTS_ENABLED` is a kill
switch, and a type with no configured URL is dropped **before** any event is
built or dispatched. An injected sink bypasses the URL check, since a cloud
overlay may fan out to destinations that are not in config.

Payloads are operational metadata only: no prompt or response text, and
credential-shaped keys (`api_key`, `authorization`, `cookie`, `secret`, …) are
removed recursively before any sink sees the event. `cache.miss` is deliberately
emitted once per provider-served request rather than on every lookup layer miss,
so the event volume tracks traffic instead of lookup count.

## Billing surface

`/api/v1/billing/*` (`api/app/modules/billing/billing_controller.py`) is a
**placeholder seam**: the router object exists so `app/main.py` has a stable
import target, and the gate (`features.is_enabled("billing")`) is the
enforcement. The full implementation (trial → active → past_due →
deprovisioned webhooks, Stripe usage records, provision tiers) lives in the
private cloud repo — metering first, then portal, then SSO.

## Invariants

1. OSS image ships no cloud routes, providers, or license logic.
2. Every seam default preserves today's behavior (fail-open, RLS, no
   in-process embeddings).
3. This doc is the record; new seams are added here when discovered.
