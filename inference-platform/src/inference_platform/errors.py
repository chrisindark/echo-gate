"""Structured HTTP errors.

The contract promises machine-readable errors: `4xx` is a caller bug, `503` is
"model not loaded, degrade to your fallback", `500` is an inference failure.
Consumers switch on `error`, never on the human-readable message.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def api_error(status_code: int, error: str, **fields: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": error, **fields})
