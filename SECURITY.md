# Security Policy

Echo Gate sits inline in front of every LLM call its operators make, and its
multi-tenancy model (RLS + API-key identity) is a security feature. Please
don't report vulnerabilities in public issues.

## Reporting a vulnerability

Use **GitHub Private Vulnerability Reporting** (the "Report a vulnerability"
button on the repository's Security tab). If that is unavailable, open a
minimal public issue asking the maintainers to enable a private channel —
include no details about the vulnerability itself.

Please include:

- A reproduction path (endpoints, request bodies/headers, config flags).
- The affected version/commit.
- Any log excerpts — **redact API keys, tenant data, and prompt/response
  content** before attaching them.

## Response expectations

- Acknowledgment within 72 hours of a report.
- Triage and, for accepted issues, a fix or mitigation on a best-effort
  timeline; coordinated disclosure once a fix ships.

## Security-relevant invariants (report deviations)

These are the properties `docs/ARCHITECTURE.md` and `docs/DATA_MODEL.md`
document; regressions are security bugs, not style nits:

- Postgres RLS applies to every tenant-scoped table; the app connects as the
  non-owner role; trusted background jobs run in an explicit system context.
- Tenant identity is established **only** by resolving `X-API-Key`
  server-side. Caller-supplied `X-Tenant-ID`/`X-User-ID` headers must never
  establish identity.
- Admin surface requires `X-Admin-Key` with scoped permissions; admin keys
  are minted only by `app/scripts/create_admin_key.py`, never over HTTP.
- Cache entries are tenant-scoped; cache scope only widens as far as the
  entity extractor allows (sensitive prompts are never stored `GLOBAL`);
  time-sensitive/finance-market prompts are not cached.
- Secrets (`X-API-Key`, `X-Admin-Key`, provider keys) are never logged.
- A cache-layer failure must degrade to a provider response, never expose
  one tenant's cached answer to another.

## Supported versions

The `main` branch receives security fixes; tagged releases are announced in
the changelog. There is no long-term-support fork at this stage.
