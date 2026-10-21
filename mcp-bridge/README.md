# Interim Echo Gate MCP bridge (dev-only)

**Status: temporary scratch tooling.** `docs/ROADMAP.md` records the decision
that Echo Gate will not ship its own MCP endpoint — the intended agentic
surface is an external, contract-driven MCP server project (see the
roadmap's decision note). Until that matures, this bridge gives `agent-forge`
a standards-based way to call the gateway. Once it covers this use case,
**delete this directory.**

It is deliberately isolated:

- Never imported by `app/`; not mounted in `app/main.py`; adds no routes, so
  the OpenAPI contract snapshot is unchanged.
- Not in `pyproject.toml`; excluded from the Docker image (`.dockerignore`).
  Freight is confined to this directory plus its own venv.
- It speaks plain HTTP to a **running** gateway, so auth (`X-API-Key`), quotas,
  caching, provider fallback, and RLS all behave exactly as for any other
  caller. The bridge holds no gateway state and cannot bypass the gateway.

## Tools

| Tool | Wraps | Notes |
| --- | --- | --- |
| `echo_gate_chat_completion` | `POST /api/v1/chat/completions` | Always non-streaming (`stream: false`); returns content, tool calls, usage, and a `cache_info` summary (hit/source/score/intent/latency). |
| `echo_gate_semantic_cache_search` | `POST /api/v1/chat/completions/semantic-search` | Diagnostic: runs only the cache path, no provider call. |
| `echo_gate_health` | `GET /api/v1/health/deps` | Non-gating dependency roll-up. |

Admin endpoints are intentionally not exposed.

## Run

```bash
python3 -m venv mcp-bridge/.venv
mcp-bridge/.venv/bin/pip install -r mcp-bridge/requirements.txt
```

Start the gateway first (`docker-compose up -d qdrant redis postgres` +
`poetry run alembic upgrade head` + `poetry run uvicorn app.main:app --port
8000` — or `./dev.sh`), then in a **separate terminal**:

```bash
export ECHO_GATE_API_KEY=<tenant key>
export ECHO_GATE_BASE_URL=http://localhost:8000   # optional, this is the default
mcp-bridge/.venv/bin/fastmcp run mcp-bridge/server.py --transport stdio
```

Instead of exporting shell variables you can copy `mcp-bridge/.env.example`
to `mcp-bridge/.env` (git-ignored) and fill it in — the server loads it on
startup via python-dotenv; process environment variables take precedence over
file values.

Any MCP client (Claude Desktop, IDE integrating MCP, `agent-forge`) connects
via stdio. For an inspectable HTTP endpoint instead, use
`--transport streamable-http` (FastMCP prints the URL).

## Verification caveat

Per `AGENTS.md` verification rules: bridge ↔ gateway behavior was not verified
against live infra (Redis, Qdrant, Postgres, provider). The tools were only
checked for import/syntax. Do a manual round-trip once the gateway is up:

```bash
curl -s localhost:8000/api/v1/health/deps   # gateway up
# then, from any MCP client, call echo_gate_health and one chat completion
```
