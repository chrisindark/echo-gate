# ADR 0005: Per-Tenant BYOK Provider Credentials

## Status
Proposed (2026-11-25) — design agreed and tracked; **not implemented**. Owner: unassigned.
Tracking: [`docs/ROADMAP.md` Phase 8](../ROADMAP.md#phase-8--data-plane--commercial-surface).
Security review required before the first slice ships.

## Context

Tenants today authenticate to Echo Gate with a gateway API key and the gateway
calls upstreams with **platform** credentials read from the environment
(`api/app/modules/llm/llm_provider_service.py:47-65`). Two separate key problems
sit behind the single word "key" and must not be conflated:

| | Gateway API key (`api_keys`) | Provider credential (BYOK) |
| :--- | :--- | :--- |
| Direction | client → gateway | gateway → OpenAI/Anthropic/… |
| Gateway operation | compare a hash | **replay verbatim** upstream |
| Storage requirement | one-way hash is sufficient | must be **recoverable** |
| Blast radius if leaked | access to this gateway, bounded by quota | the tenant's provider account and its spend |
| Failure semantics | reject request (401) | must **never** fall back to platform credentials |

`api_keys` stores `sha256(plaintext)` (`api/app/modules/api_keys/api_keys_service.py:23-25`)
because authentication is an equality test, so plaintext is never needed. Upstream
calls are not an equality test: `api/app/modules/llm/providers/openai.py:44` builds
`Authorization: Bearer {self.openai_api_key}`. A hash cannot be replayed, so
encryption — not hashing — is the only viable at-rest protection for BYOK.

BYOK itself is a standard expectation for a gateway: teams that already pay a
provider want the gateway's caching, routing, and observability without moving
their spend onto the operator's account.

## Decision

1. **BYOK is a second resource, not a variant of `api_keys`.** A new
   `provider_credentials` table keyed by `(tenant_id, provider, label)`. `api_keys`
   keeps answering "who is calling?" and is never used to store provider secrets.
2. **Envelope encryption with the key-encryption key (KEK) outside the database.**
   Per-row AES-256-GCM over the secret, `nonce` and `key_version` stored alongside,
   `AAD = f"{tenant_id}:{provider}:{row_id}"` so a ciphertext cannot be transplanted
   between tenants even by an attacker with write access to the table. The KEK comes
   from `BYOK_MASTER_KEY` (single-node/self-hosted) or a KMS/secret manager
   (cloud) and is **never** stored in Postgres. When BYOK is enabled and no KEK is
   configured, startup fails loudly rather than silently disabling the feature.
   Per-credential DEKs are the documented upgrade path if KEK rotation ever needs
   to rewrap thousands of rows without decrypting secrets.
3. **Real RLS on this table.** Unlike `api_keys` (permissive policy because
   resolution precedes tenant context — `alembic/versions/e7f8a9b0c1d2_add_rls_policies_for_key_tables.py:42-46`),
   BYOK is resolved *after* auth, so the tenant predicate from the baseline applies
   (`alembic/versions/3f9c1a7b2d40_baseline_postgres_rls.py:33-34`):
   `tenant_id = current_setting('app.current_tenant', true) OR current_setting('app.rls_system', true) = 'on'`.
4. **Credentials are passed per call, never assigned to the shared adapter.** The
   provider facade is a singleton whose keys are captured at construction
   (`llm_provider_service.py:47-65`). Mutating `self.openai_api_key` per request
   would let concurrent requests from two tenants interleave and bill one tenant's
   traffic to another's key. Adapters take an optional `api_key` argument (or a
   per-request adapter instance); the singleton is never mutated.
5. **Fail closed, with no silent env fallback.** If a tenant has a BYOK credential
   for a provider and it is inactive, revoked, or fails to decrypt, the request is
   rejected with a clear error. Falling back to `config.*_API_KEY` would convert a
   tenant's outage into operator spend. This is *not* a violation of the fail-open
   invariant: fail-open applies to cache and inference, never to credential
   resolution.
6. **Breaker scope includes credential identity.** A revoked or rate-limited BYOK
   key must not trip the provider-wide circuit breaker and degrade tenants using
   platform keys. Key breakers on `(provider, credential_id)`.
7. **Platform quota is skipped; metering continues.** A BYOK call consumes none of
   the operator's rate-limit capacity, so `LlmQuotaGuard` is bypassed, but token
   usage is still recorded and attributed as `billed=false` / `upstream=tenant` —
   otherwise tenants lose visibility into exactly the traffic they care about.
8. **The cache key is left unchanged.** `_generate_cache_key` already includes
   `tenant_id` (`api/app/modules/llm/llm_router_service.py:842`), so a BYOK tenant
   cannot receive another tenant's cached response, and a cache hit costs that
   tenant nothing upstream. Adding a credential fingerprint to the key would
   fragment the cache for no correctness gain.
9. **Secrets never leave the service layer.** List endpoints return metadata only
   (`id`, `provider`, `label`, `key_suffix`, `fingerprint`, `is_active`,
   `created_at`, `last_used_at`). Only `resolve_for_call(tenant, provider)`
   decrypts, and it is reachable only from the router. Audit events
   (`byok.credential.created` / `.revoked` / `.used`) carry no secret material and
   must be added to the sanitizer deny-list; upstream error bodies are never
   returned verbatim because some SDKs echo credentials.

### Proposed schema

```
provider_credentials
  id                    int pk
  tenant_id             varchar  index          -- RLS predicate
  provider              varchar                  -- 'openai' | 'claude' | ...
  label                 varchar                  -- 'prod', 'personal'
  key_suffix            varchar(4)               -- display only, last 4 chars
  fingerprint           char(64)                 -- sha256(plaintext), duplicate detection only
  ciphertext            bytea
  nonce                 bytea
  key_version           int
  is_active             boolean
  revoked_at            timestamptz null
  last_used_at          timestamptz null         -- throttled write, not per request
  created_at, updated_at
  UNIQUE (tenant_id, provider, label)
```

`fingerprint` exists solely so a re-pasted duplicate can be detected without
decrypting every row. It is never an authentication factor and is not indexable
as an identity.

## Consequences

- Tenants can bring their own provider account; operator spend on that tenant's
  traffic drops to zero while caching, routing, and analytics keep working.
- The deployment gains a secret that is more valuable than a gateway key, so the
  operational bar rises: KEK provisioning becomes a startup requirement, and KEK
  custody moves to a secret manager or KMS rather than a compose file.
- `service_availability()` is currently a startup snapshot used by `/v1/models`
  (`llm_provider_service.py:67-88`). With BYOK it must become tenant-aware, or
  model listing will under-report for tenants whose access comes from their own
  credential.
- Two credential sources now exist on the call path. The precedence rule must be
  explicit and tested in one place: **tenant credential if present, otherwise
  platform env credential.**

## Known limitations and residual risk

- **Host compromise is not mitigated by this design.** If an attacker can read the
  process environment or the secret manager, they can decrypt. Envelope encryption
  defends the *database* (backups, snapshots, read-only SQL injection), which is
  the realistic and most common loss vector. Report this honestly in the security
  review rather than implying end-to-end secrecy.
- **Log and error-body leakage is the highest-likelihood practical leak.** Latency
  logs, request middleware, event payloads, and upstream error passthrough all need
  explicit redaction, with a regression test asserting that a submitted secret
  never appears in captured logs, events, or error responses.
- **Cache hits are free for BYOK tenants.** This is desirable but must be stated
  in the product docs; a tenant may otherwise be surprised that a cached answer
  produced no provider charge.
- **No KEK rotation tooling in the first slice.** `key_version` is written from day
  one so rotation is possible later without a schema change, but the rewrap job is
  deferred and documented as absent.
- **Provider terms of service vary.** Some providers restrict resale, sub-account
  use, or key sharing across organizations. This is a documentation/compliance
  concern per provider, not an implementation constraint, and belongs in the BYOK
  docs page before the feature is announced.
- **A revoked key is only as immediate as the next request.** Revocation is
  effective per call because decryption happens per call (no in-process plaintext
  cache), which is the intended tradeoff.

## Delivery slices (tracking)

1. [ ] `provider_credentials` table + RLS policy + declare `cryptography` as an
   explicit dependency (currently transitive only).
2. [ ] `ProviderCredentialService`: `create` / `list_metadata` / `revoke` /
   `resolve_for_call`, mirroring `api_keys_service` shapes for contract stability.
3. [ ] Per-call credential parameter through every adapter; credential-scoped
   breakers; tenant-aware `service_availability()`.
4. [ ] Fail-closed resolution with an explicit error; a test asserting no env
   fallback occurs when a BYOK credential is present but unusable.
5. [ ] Audit events + redaction deny-list + log/error leak regression test.
6. [ ] UI in `web/src/app/settings/page.tsx` beside the existing key manager;
   metadata only, secret shown once on creation, never in browser storage.
7. [ ] Docs: BYOK page (precedence rules, cache-hit behavior, provider ToS note,
   revocation semantics, host-compromise limitation).

Every route/schema slice updates `tests/contract/test_openapi_snapshot.py`.

## Non-goals

- Reusing or extending `api_keys` to hold provider secrets.
- Hashing provider secrets (impossible: they must be replayed).
- Client-supplied per-request provider keys (a passthrough header would bypass
  server-side caching and usage metering, which is most of the gateway's value).
- Sharing one credential across tenants, or across users within a tenant.
- KEK rotation tooling, per-tenant DEKs, and HSM/KMS abstraction in the first slice.
- Any change to cache-key composition or cross-tenant cache scope.
