# Contributing to Echo Gate

Thank you for considering a contribution. This repo is licensed under the
GNU AGPL-3.0; by submitting a pull request you agree your contribution is
licensed under AGPL-3.0-only (see `LICENSE`, `NOTICE`, and the License
section of the README).

## Getting started

Follow the full setup in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) and the
README quickstart. Short version:

```bash
docker-compose up -d qdrant redis postgres
poetry install
cp app/.env.example app/.env.local        # then fill in provider keys
poetry run alembic upgrade head
poetry run uvicorn app.main:app --port 8000 --reload
poetry run pytest                         # full suite; no live infra needed
```

The UI is a separate Next.js app in `ui/` (`npm install`, `npm run dev`,
`npm run lint`, `npm run build`).

## Ground rules

- **Read `docs/ROADMAP.md` first** — it is the plan of record and records
  intentional decisions; don't reopen them silently in a diff.
- Read the relevant deep-reference doc (`ARCHITECTURE`, `API_REFERENCE`,
  `DATA_MODEL`, `CONFIGURATION`) before a non-trivial change.
- Keep container/controller code thin; business logic lives in services;
  request/response contracts live in `*_schema.py`.
- Async I/O for network/provider calls; cache failures must stay fail-open;
  tenant identity comes only from a resolved `X-API-Key`.
- New environment variables must be added to `app/core/config.py` and
  documented in `docs/CONFIGURATION.md`.

## API contract

The public API surface is contract-tested: `tests/test_openapi_snapshot.py`
compares `app.openapi()` against `tests/snapshots/openapi.json`. If the test
fails with a classified diff, confirm the changes are additive, then commit
the regenerated snapshot. Removing or reshaping existing endpoints needs a
reviewed, intentional change, not a snapshot overwrite.

## Commit style and testing

- Small, focused commits; imperative subject lines
  (e.g. `chat: honor max_tokens in cache key`).
- `pre-commit run --all-files` passes (ruff check + format, UI prettier).
- Add focused tests for changed behavior. `poetry run pytest` and
  `poetry run python -m compileall app` must pass before every push;
  `npm run lint` and `npm run build` for UI changes.

## Security

Do not open public issues for security vulnerabilities. Follow
[SECURITY.md](SECURITY.md).
