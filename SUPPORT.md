# Support

Channels for getting help, in order of preference:

1. **Questions / usage discussions** — open a
   [Discussion](https://github.com/chrisindark/echo-gate/discussions)
   (create one in the `Q&A` category).
2. **Bug reports** — open an issue with the
   [bug template](https://github.com/chrisindark/echo-gate/issues/new?template=bug_report.yml).
   Please include: how you run the stack (`docker-compose` / local), the
   `PYTHON_ENV` value, relevant log snippet (redact keys and prompt data),
   and the endpoint + request body that failed.
3. **Feature requests** — check [docs/ROADMAP.md](docs/ROADMAP.md) first; if
   it's not planned, open an issue through the
   [feature template](https://github.com/chrisindark/echo-gate/issues/new?template=feature_request.yml).

Self-hosted troubleshooting starts at
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (setup + commands) and
[docs/CONFIGURATION.md](docs/CONFIGURATION.md) (every environment variable).
Health surfaces: `GET /api/v1/health/deps` reports Postgres/Redis/Qdrant/Celery state.
