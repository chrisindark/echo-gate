# ADR 0003: PostgreSQL Row-Level Security (RLS) Multi-Tenancy

## Status
Accepted (2026-10-20)

## Context
In a multi-tenant gateway, tenant data leaks represent a critical security vulnerability. Relying exclusively on application-level `WHERE tenant_id = ...` clauses is error-prone when queries are authored across numerous services.

## Decision
1. Multi-tenancy is enforced at the database engine layer using PostgreSQL Row-Level Security (RLS).
2. The FastAPI application connects using a non-owner database role. Non-owner roles cannot bypass RLS policies.
3. Every tenant-partitioned table defines `ENABLE ROW LEVEL SECURITY` and filters via `current_setting('app.current_tenant')`.
4. Background workers requiring cross-tenant aggregation use an explicitly trusted system context.

## Consequences
- Guaranteed data isolation even if an application query omits an explicit tenant filter.
- Database migrations must include explicit RLS policy statements.
