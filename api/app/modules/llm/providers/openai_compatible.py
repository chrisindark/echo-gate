"""Shared OpenAI-compatible streaming implementation.

Extracted verbatim from the former single-file `LlmProviderService`. The methods
still operate on the dispatcher instance (``self``), which owns every provider
credential and client; only the owning class changed.
"""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import config
from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
)
from app.modules.llm.providers.common import (
    extract_http_error_detail as _extract_http_error_detail,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    async def _stream_openai_compatible(
        self,
        url: str,
        headers: dict[str, str],
        payload_data: dict[str, Any],
        service_label: str,
    ) -> AsyncIterator[ChatCompletionChunk]:
        try:
            async with httpx.AsyncClient() as client:
                logger.info(f"Forwarding stream request to {service_label}...")
                async with client.stream(
                    "POST",
                    url,
                    json=payload_data,
                    headers=headers,
                    timeout=config.HTTP_CLIENT_TIMEOUT_SECONDS,
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        detail = _extract_http_error_detail(
                            response, f"{service_label} error"
                        )
                        logger.error(
                            f"{service_label} returned HTTP error: {response.status_code} - {response.text}"
                        )
                        raise HTTPException(
                            status_code=response.status_code, detail=detail
                        )

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk_data = json.loads(data_str)
                                if isinstance(chunk_data, dict):
                                    chunk_data.setdefault(
                                        "id", f"chatcmpl-{uuid.uuid4().hex}"
                                    )
                                    chunk_data.setdefault("created", int(time.time()))
                                    chunk_data.setdefault(
                                        "model",
                                        payload_data.get("model", "unknown"),
                                    )
                                    chunk_data.setdefault("choices", [])
                                    yield ChatCompletionChunk.model_validate(chunk_data)
                            except Exception as e:
                                logger.warning(
                                    f"Failed to parse chunk from {service_label}: {e} - line: {line}"
                                )
        except HTTPException:
            raise
        except httpx.HTTPStatusError as e:
            detail = _extract_http_error_detail(e.response, f"{service_label} error")
            logger.error(
                f"{service_label} returned HTTP error: {e.response.status_code}"
            )
            raise HTTPException(status_code=e.response.status_code, detail=detail)
        except Exception as e:
            logger.error(f"Failed to stream from {service_label}: {e}")
            raise HTTPException(status_code=502, detail=f"Bad Gateway: {e}")
