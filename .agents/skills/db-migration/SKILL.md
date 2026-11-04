---
name: db-migration
description: Instructions for managing SQLAlchemy database models, Alembic migrations, and PostgreSQL Row-Level Security (RLS) policies in Echo Gate.
---

# Database Migrations & Row-Level Security (RLS)

Echo Gate connects to PostgreSQL using a non-owner database user role so that PostgreSQL Row-Level Security (RLS) policies enforce strict per-tenant data isolation at the engine level.

## 1. Schema & Migration Rules
- Models live in `api/app/modules/<feature>/*_model.py`.
- Migrations live in `alembic/versions/`.
- **Never rely on `Base.metadata.create_all()`**: All production and test environments migrate using Alembic.
- **Tenant ID Column**: Every tenant-partitioned table must include a non-nullable `tenant_id` column indexed for fast filtering.
- **RLS Policy Declaration**: Every new tenant-partitioned table must define an RLS policy (`ENABLE ROW LEVEL SECURITY`) and attach the standard tenant check policy (`tenant_id = current_setting('app.current_tenant')`).

## 2. Generating & Applying Migrations
```bash
# Generate a new migration after updating models:
poetry run alembic revision --autogenerate -m "describe_change"

# Review the generated file in alembic/versions/ to verify RLS directives!

# Apply migration:
poetry run alembic upgrade head
```

## 3. Verification
- Test models and multi-tenancy:
  ```bash
  poetry run pytest api/app/modules/llm_quota/tests/
  ```
