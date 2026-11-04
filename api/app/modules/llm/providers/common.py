"""Shared helpers for LLM provider adapters.

The two module-level helpers here (``_extract_http_error_detail`` and
``_prepare_openai_compatible_payload``) originally lived in
``llm_provider_service.py`` and are used by every OpenAI-compatible adapter
(OpenAI, Groq, OpenRouter, Ollama). Keeping them here avoids duplicating
them per provider while keeping the dispatcher thin.
"""

import logging
from typing import Any

import httpx

from app.modules.chat.chat_schema import ChatCompletionRequest

logger = logging.getLogger(__name__)


def extract_http_error_detail(response: httpx.Response, fallback_msg: str) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and "message" in err:
                return f"{fallback_msg}: {err['message']}"
            elif isinstance(err, str):
                return f"{fallback_msg}: {err}"
            elif "message" in data:
                return f"{fallback_msg}: {data['message']}"
    except Exception:
        pass
    text = response.text
    if text:
        return f"{fallback_msg}: {text}"
    return fallback_msg


def prepare_openai_compatible_payload(
    request: ChatCompletionRequest, stream: bool = False
) -> dict[str, Any]:
    payload_data = request.model_dump(exclude_unset=True)
    # `user_id`/`tenant_id` are private attributes on the request, so they are
    # already absent from the dump; they are listed here only as documentation of
    # what must never reach a provider.
    for key in [
        "service_name",
        "thinking",
        "thinking_budget",
        "session_id",
        "conversation_id",
        "cache_info",
    ]:
        payload_data.pop(key, None)

    if request.reasoning_effort:
        payload_data["reasoning_effort"] = request.reasoning_effort
    elif request.thinking:
        payload_data["reasoning_effort"] = "medium"
    else:
        payload_data.pop("reasoning_effort", None)

    if payload_data.get("tools") is None:
        payload_data.pop("tools", None)
    if payload_data.get("tool_choice") is None:
        payload_data.pop("tool_choice", None)
    if stream:
        payload_data["stream"] = True
    return payload_data
