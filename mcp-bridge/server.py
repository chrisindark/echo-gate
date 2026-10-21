"""Interim MCP bridge exposing Echo Gate tools over the MCP standard.

This is **local dev tooling only**. It is deliberately outside `app/`:

- It is not a router of the FastAPI app, never mounted in `app/main.py`, and
  does not change the contract-tested OpenAPI surface (`app.openapi()`).
- It is not part of `pyproject.toml`; it runs from its own venv
  (`mcp-bridge/requirements.txt`).
- `docs/ROADMAP.md` records the decision that Echo Gate will not ship its own
  MCP endpoint — the planned agentic surface is an external, contract-driven
  MCP server project. Once that covers this use case, delete this directory.

Run (separate terminal, gateway already up):
    python3 -m venv mcp-bridge/.venv
    mcp-bridge/.venv/bin/pip install -r mcp-bridge/requirements.txt
    cp mcp-bridge/.env.example mcp-bridge/.env   # then fill in ECHO_GATE_API_KEY
    mcp-bridge/.venv/bin/fastmcp run mcp-bridge/server.py --transport stdio

Configuration (loading order: process environment wins, then mcp-bridge/.env
via python-dotenv, then built-in defaults):
    ECHO_GATE_BASE_URL        default http://localhost:8000
    ECHO_GATE_API_KEY         tenant API key, sent as X-API-Key
    ECHO_GATE_TIMEOUT_SECONDS per-request timeout, default 120

The bridge is a dumb HTTP client: auth, quotas, caching, and fallback
semantics all stay on the gateway side, exactly like any other caller.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

_BRIDGE_DIR = Path(__file__).resolve().parent
load_dotenv(_BRIDGE_DIR / ".env")

mcp = FastMCP("echo-gate")

BASE_URL = os.getenv("ECHO_GATE_BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("ECHO_GATE_API_KEY", "")
REQUEST_TIMEOUT = float(os.getenv("ECHO_GATE_TIMEOUT_SECONDS", "120"))

CHAT_COMPLETIONS = f"{BASE_URL}/api/v1/chat/completions"
SEMANTIC_SEARCH = f"{CHAT_COMPLETIONS}/semantic-search"
HEALTH_DEPS = f"{BASE_URL}/api/v1/health/deps"


@asynccontextmanager
async def _http() -> Any:
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    async with httpx.AsyncClient(
        base_url=BASE_URL, headers=headers, timeout=REQUEST_TIMEOUT
    ) as client:
        yield client


def _check_key() -> None:
    if not API_KEY:
        raise ToolError(
            "ECHO_GATE_API_KEY is not set. Set it to the tenant API key "
            "(a supplied but unresolved key is rejected by the gateway)."
        )


def _format_status_error(response: httpx.Response) -> ToolError:
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    return ToolError(f"Echo Gate returned {response.status_code}: {detail}")


def _validate_messages(messages: list[dict[str, Any]]) -> None:
    if not messages:
        raise ToolError("messages must be a non-empty list of {role, content}.")
    for message in messages:
        if not isinstance(message, dict) or "role" not in message:
            raise ToolError(
                f"Each message needs at least {{'role', 'content'}}; got {message!r}."
            )


def _cache_and_latency(completion: dict[str, Any]) -> dict[str, Any]:
    usage = completion.get("usage")
    cache_info = completion.get("cache_info")
    summary: dict[str, Any] = {}
    if usage:
        summary["usage"] = usage
    if cache_info:
        summary["cache_hit"] = cache_info.get("cache_hit")
        summary["cache_source"] = cache_info.get("source")
        summary["cache_score"] = cache_info.get("score")
        summary["intent"] = cache_info.get("intent")
        breakdown = (cache_info.get("latency_breakdown") or {}).get("total_ms")
        if breakdown is not None:
            summary["latency_ms"] = breakdown
    return summary


@mcp.tool(
    tags={"echo-gate", "chat"},
    annotations={"readOnlyHint": False, "openWorldHint": True},
)
async def echo_gate_chat_completion(
    messages: list[dict[str, Any]],
    model: str,
    service_name: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    session_id: str | None = None,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send an OpenAI-style chat completion through the Echo Gate gateway.

    Args:
        messages: Chat history, e.g. [{"role": "user", "content": "hi"}].
            Roles: system | user | assistant | tool.
        model: Model name; must be in the gateway's ALLOWED_MODELS
            (e.g. "gemini-3.5-flash-lite", "llama-3.1-8b-instant").
        service_name: Provider (gemini, openai, groq, openrouter, claude,
            ollama).  Omit to let the gateway default apply.
        temperature: Optional sampling temperature (part of the cache key).
        max_tokens: Optional completion token cap.
        session_id: Optional correlation id (cache scope + traces).
        response_format: Optional {"type": "json_object"} or
            {"type": "json_schema", ...}.

    Always non-streaming on the bridge side (the gateway's SSE streaming is
    not exposed over MCP). On a semantic-cache hit the gateway serves the
    cached completion; cache_info in the result reports where it came from.
    """
    _check_key()
    _validate_messages(messages)
    request: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if service_name is not None:
        request["service_name"] = service_name
    if temperature is not None:
        request["temperature"] = temperature
    if max_tokens is not None:
        request["max_tokens"] = max_tokens
    if session_id is not None:
        request["session_id"] = session_id
    if response_format is not None:
        request["response_format"] = response_format

    try:
        async with _http() as client:
            response = await client.post(CHAT_COMPLETIONS, json=request)
    except httpx.HTTPError as e:
        raise ToolError(f"Echo Gate is unreachable at {BASE_URL}: {e}") from e
    if response.status_code != 200:
        raise _format_status_error(response)

    completion = response.json()
    choice = completion["choices"][0]
    message = choice["message"]
    result = {
        "content": message.get("content"),
        "finish_reason": choice.get("finish_reason"),
        "model": completion.get("model"),
    }
    if message.get("tool_calls"):
        result["tool_calls"] = message["tool_calls"]
    result.update(_cache_and_latency(completion))
    return result


@mcp.tool(
    tags={"echo-gate", "cache", "diagnostic"},
    annotations={"readOnlyHint": True},
)
async def echo_gate_semantic_cache_search(
    message: str, model: str, service_name: str | None = None
) -> dict[str, Any]:
    """Run the gateway's semantic cache search without calling a provider.

    Diagnostic tool: reports whether the prompt would hit the semantic cache
    (matches, intent classification, scores, latency breakdown). Useful for
    agents to debug why a prompt is (or is not) served from cache.
    """
    _check_key()
    request: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "stream": False,
    }
    if service_name is not None:
        request["service_name"] = service_name

    try:
        async with _http() as client:
            response = await client.post(SEMANTIC_SEARCH, json=request)
    except httpx.HTTPError as e:
        raise ToolError(f"Echo Gate is unreachable at {BASE_URL}: {e}") from e
    if response.status_code != 200:
        raise _format_status_error(response)
    return response.json().get("response", response.json())


@mcp.tool(
    tags={"echo-gate", "health"},
    annotations={"readOnlyHint": True},
)
async def echo_gate_health() -> dict[str, Any]:
    """Report Echo Gate dependency health (Postgres, Redis, Qdrant, Celery).

    Non-gating roll-up (/health/deps): status is healthy | degraded |
    unhealthy. Note Redis/Qdrant degradation only means cache misses; the
    gateway still serves from providers.
    """
    try:
        async with _http() as client:
            response = await client.get(HEALTH_DEPS)
    except httpx.HTTPError as e:
        raise ToolError(f"Echo Gate is unreachable at {BASE_URL}: {e}") from e
    if response.status_code != 200:
        raise _format_status_error(response)
    return response.json()


if __name__ == "__main__":
    mcp.run()  # stdio by default
