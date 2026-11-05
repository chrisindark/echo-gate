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
```

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
