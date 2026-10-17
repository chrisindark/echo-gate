"""Unit tests for API key hashing/identity helpers (no database required)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.core.guards import ApiKeyAuthGuard
from app.modules.api_keys.api_keys_model import ApiKey
from app.modules.api_keys.api_keys_schema import (
    ApiKeyCreatedResponse,
    ApiKeyResponse,
)
from app.modules.api_keys.api_keys_service import ApiKeyService, hash_api_key


def test_hash_api_key_is_deterministic_and_not_reversible_prefix():
    key = "eg_example_key_value"
    digest = hash_api_key(key)
    assert digest == hash_api_key(key)
    assert len(digest) == 64
    assert key not in digest


def test_hash_api_key_differs_per_key():
    assert hash_api_key("eg_one") != hash_api_key("eg_two")


def test_generate_key_has_prefix_and_entropy():
    first = ApiKeyService.generate_key()
    second = ApiKeyService.generate_key()
    assert first.startswith("eg_")
    assert first != second
    assert len(first) > len("eg_") + 20


def test_resolve_returns_none_for_empty_key():
    assert ApiKeyService().resolve_api_key("") is None


def test_api_key_created_response_construction_from_model():
    record = ApiKey(
        id=1,
        name="local-dev",
        key_prefix="eg_12345678",
        key_hash="hash",
        tenant_id="dev",
        user_id="dev",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    base = ApiKeyResponse.model_validate(record)
    payload = ApiKeyCreatedResponse(**base.model_dump(), api_key="eg_plaintext_key")
    assert payload.api_key == "eg_plaintext_key"
    assert payload.id == 1
    assert payload.tenant_id == "dev"


@pytest.mark.asyncio
async def test_api_key_auth_guard_rejects_missing_key():
    guard = ApiKeyAuthGuard()
    request = MagicMock()
    request.headers.get.return_value = None
    with pytest.raises(HTTPException) as exc_info:
        await guard(request=request, x_api_key=None)
    assert exc_info.value.status_code == 401
    assert "API Key missing" in exc_info.value.detail
