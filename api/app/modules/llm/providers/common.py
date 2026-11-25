"""Shared helpers for LLM provider adapters.

The two module-level helpers here (``_extract_http_error_detail`` and
``_prepare_openai_compatible_payload``) originally lived in
``llm_provider_service.py`` and are used by every OpenAI-compatible adapter
(OpenAI, Groq, OpenRouter, Ollama). Keeping them here avoids duplicating
them per provider while keeping the dispatcher thin.
"""

import logging
import re
from typing import Any

import httpx
from fastapi import HTTPException

from app.modules.chat.chat_schema import ChatCompletionRequest

logger = logging.getLogger(__name__)

_DATA_URI_RE = re.compile(r"^data:([^;,]+);base64,(.*)$", re.DOTALL)


def _image_source_to_openai_url(source: dict[str, Any]) -> str:
    src_type = source.get("type")
    if src_type == "base64":
        media_type = source.get("media_type") or "image/png"
        return f"data:{media_type};base64,{source.get('data', '')}"
    if src_type == "url" and isinstance(source.get("url"), str):
        return source["url"]
    raise HTTPException(
        status_code=400, detail=f"Unsupported image source type: {src_type!r}"
    )


def to_openai_content_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate ordered internal parts to OpenAI's content-part wire shape."""
    converted: list[dict[str, Any]] = []
    for part in parts:
        part_type = part.get("type")
        if part_type in ("text", "image_url"):
            converted.append(part)
        elif part_type == "image":
            converted.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_source_to_openai_url(part["source"])},
                }
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"{part_type!r} content blocks are not supported by this provider",
            )
    return converted


def to_anthropic_content_blocks(
    parts: list[dict[str, Any]], cache_control: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Translate ordered internal parts to Anthropic content blocks."""
    blocks: list[dict[str, Any]] = []
    for part in parts:
        part_type = part.get("type")
        if part_type == "text":
            block: dict[str, Any] = {"type": "text", "text": part["text"]}
        elif part_type == "image_url":
            url = part["image_url"]["url"]
            match = _DATA_URI_RE.match(url)
            if match:
                block = {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": match.group(1),
                        "data": match.group(2),
                    },
                }
            else:
                block = {"type": "image", "source": {"type": "url", "url": url}}
        elif part_type in ("image", "document"):
            block = dict(part)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported content part type: {part_type!r}",
            )
        if cache_control and not blocks:
            block["cache_control"] = cache_control
        blocks.append(block)
    return blocks


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
        "prompt_optimization",
        "cache_enabled",
        "cache_ttl",
        "retries",
        "stream_pacing",
        "stream_pacing_delay_ms",
    ]:
        payload_data.pop(key, None)

    # Bounded passthrough: merge `extra_body` into the top-level payload so its
    # keys reach the selected provider, then drop the wrapper key itself.
    extra_body = payload_data.pop("extra_body", None)
    if isinstance(extra_body, dict):
        merged = dict(extra_body)
        merged.update(payload_data)
        payload_data = merged

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

    # Content parts are private on ChatMessage, so restore them explicitly on
    # the dumped messages and keep their original order.
    dumped_messages = payload_data.get("messages")
    if isinstance(dumped_messages, list):
        for original, dumped in zip(request.messages, dumped_messages, strict=False):
            if original.content_parts:
                dumped["content"] = to_openai_content_parts(original.content_parts)

    if stream:
        payload_data["stream"] = True
    return payload_data
