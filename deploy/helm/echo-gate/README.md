# Echo Gate Helm Chart (enterprise self-Hosted)

Umbrella chart for on-prem / VPC deployments: gateway API, Celery worker and
beat, run-migrations-on-deploy Job, and (by default) bundled Postgres, Redis,
and Qdrant. The observability UI (`web/`) is optional.

```bash
helm dependency build deploy/helm/echo-gate
helm install echo-gate deploy/helm/echo-gate -n echo-gate --create-namespace
```

## What's here

| Piece | Source |
| --- | --- |
| Gateway API + worker + beat + migrations | sub-chart `charts/echo-gate-api` (also installable standalone) |
| Postgres / Redis / Qdrant | Bitnami charts (OCI): `registry-1.docker.io/bitnamicharts/{postgres,redis,qdrant}` |
| UI | `templates/web-deployment.yaml` (`web.enabled=false` by default) |

The gateway refuses to start without the inference platform — a separate
service. Point it at the platform chart
(`inference-platform/deploy/helm/inference-platform`):

```bash
--set echo-gate-api.config.inferenceBaseUrl=http://inference-platform.default.svc:7997
```

## Key values

| Value | Default | Notes |
| --- | --- | --- |
| `echo-gate-api.config.cacheMode` | `active` | `shadow` for pre-activation validation |
| `echo-gate-api.config.inferenceBaseUrl` | platform default | **Required** — must point at the inference platform |
| `echo-gate-api.existingSecret` | `""` | Pre-created Secret to mount (provider keys, `DATABASE_URL`, `MIGRATION_DATABASE_URL`). Secrets never come from `values.yaml` in plain text |
| `echo-gate-api.extraEnvFromSecrets` | `[]` | Additional Secrets (e.g. cert-manager issued TLS) |
| `echo-gate-api.ingress.enabled` | `false` | Enable to provision an Ingress with a TLS `secretName` (cert-manager Certificate) |
| `postgres.enabled` etc. | `true` | Disable to point at managed Postgres/Redis/Qdrant; override `echo-gate-api.config.{redisUrl,celeryBrokerUrl,qdrantUrl,databaseUrl}` |

## Migrations

`echo-gate-api` ships a **run-migrations-on-deploy** Job
(`<release>-echo-gate-api-migrations`) that runs
`alembic upgrade head` as the table owner (`MIGRATION_DATABASE_URL`). The
app's own `DATABASE_URL` connects as the non-owner `echo_gate_app` role, so
RLS applies to every tenant query. The Job is idempotent, checkpointed,
and safe to re-run:

```bash
kubectl -n echo-gate logs job/echo-gate-echo-gate-api-migrations
```

## Probes

- **Readiness** (`/api/v1/health/ready`) gates on Postgres alone — only that
  dependency's failure makes the gateway answer incorrectly. Redis/Qdrant
  failures degrade fail-open (see ARCHITECTURE.md).
- **Roll-up** (`/api/v1/health/deps`) is non-gating — alert on it, don't
  evict replicas for it.

## TLS & secrets (cert-manager pattern)

```bash
# Certificate Secrets are mounted, never checked into values.yaml
--set echo-gate-api.existingSecret=my-secret
--set echo-gate-api.extraEnvFromSecrets={cert-manager-gateway-tls}
--set echo-gate-api.ingress.enabled=true \
--set echo-gate-api.ingress.tls[0].secretName=cert-manager-gateway-tls
```

The gateway serves HTTP inside the cluster; TLS terminates at the Ingress.
