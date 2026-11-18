"""Credential extraction from X-API-Key and Authorization: Bearer.

The Bearer alias is what makes OpenAI-compatible SDK clients work without a
client-side header override. These tests pin the precedence rule (an explicit
X-API-Key wins), the scheme handling, and the fact that the quota guard buckets
a Bearer caller on the resolved key rather than the anonymous fingerprint.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi import Header, HTTPException, Response
from starlette.datastructures import Headers

from app.core.guards import ApiKeyAuthGuard, llm_quota_guard
from app.core.identity import api_key_from_headers, api_key_from_values
from app.modules.api_keys.api_keys_service import ApiKeyIdentity


@pytest.mark.parametrize(
    ("api_key", "authorization", "expected"),
    [
        ("eg_header", None, "eg_header"),
        ("  eg_header  ", None, "eg_header"),
        (None, "Bearer eg_bearer", "eg_bearer"),
        (None, "bearer eg_bearer", "eg_bearer"),
        (None, "Bearer    eg_bearer", "eg_bearer"),
        ("eg_header", "Bearer eg_bearer", "eg_header"),
        (None, "Basic dXNlcjpwYXNz", None),
        (None, "Bearer", None),
        (None, "Bearer   ", None),
        (None, "", None),
        (None, None, None),
    ],
)
def test_api_key_from_values(api_key, authorization, expected):
    assert api_key_from_values(api_key, authorization) == expected


def test_api_key_from_headers_prefers_the_gateway_header():
    headers = Headers(
        {
            "x-api-key": "eg_header",
            "authorization": "Bearer eg_bearer",
        }
    )
    assert api_key_from_headers(headers) == "eg_header"


def test_api_key_from_headers_reads_bearer():
    headers = Headers({"authorization": "Bearer eg_bearer"})
    assert api_key_from_headers(headers) == "eg_bearer"


def test_fastapi_header_default_objects_are_ignored():
    """Direct guard calls leave Header(default=None) objects in the args."""
    assert api_key_from_values(None, Header(default=None)) is None


def _stub_resolution(monkeypatch, *, tenant_id="acme", user_id="dev") -> dict:
    seen: dict[str, str] = {}
    identity = ApiKeyIdentity(api_key_id=1, tenant_id=tenant_id, user_id=user_id)

    def _resolve(key: str):
        seen["key"] = key
        return identity

    import app.modules.api_keys.api_keys_service as svc

    monkeypatch.setattr(svc.api_key_service, "resolve_api_key", _resolve)
    return seen


def _auth_request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())


@pytest.mark.asyncio
async def test_auth_guard_accepts_a_bearer_token(monkeypatch):
    seen = _stub_resolution(monkeypatch)
    request = _auth_request()

    accepted = await ApiKeyAuthGuard()(
        request=request,
        x_api_key=None,
        authorization="Bearer eg_bearer",
    )

    assert accepted is True
    assert seen["key"] == "eg_bearer"
    assert request.state.api_key == "eg_bearer"
    assert request.state.tenant_id == "acme"
    assert request.state.user_id == "dev"


@pytest.mark.asyncio
async def test_auth_guard_prefers_x_api_key_over_bearer(monkeypatch):
    seen = _stub_resolution(monkeypatch)

    await ApiKeyAuthGuard()(
        request=_auth_request(),
        x_api_key="eg_header",
        authorization="Bearer eg_bearer",
    )

    assert seen["key"] == "eg_header"


@pytest.mark.asyncio
async def test_auth_guard_rejects_an_unknown_bearer(monkeypatch):
    import app.modules.api_keys.api_keys_service as svc

    monkeypatch.setattr(svc.api_key_service, "resolve_api_key", lambda _key: None)

    with pytest.raises(HTTPException) as exc_info:
        await ApiKeyAuthGuard()(
            request=_auth_request(),
            x_api_key=None,
            authorization="Bearer eg_unknown",
        )

    assert exc_info.value.status_code == 401
    assert "Invalid or inactive" in exc_info.value.detail


@pytest.mark.asyncio
async def test_auth_guard_rejects_a_non_bearer_authorization():
    with pytest.raises(HTTPException) as exc_info:
        await ApiKeyAuthGuard()(
            request=_auth_request(),
            x_api_key=None,
            authorization="Basic dXNlcjpwYXNz",
        )

    assert exc_info.value.status_code == 401


def _quota_request(body: dict) -> SimpleNamespace:
    request = SimpleNamespace(
        state=SimpleNamespace(tenant_id="acme", user_id="dev", user="dev"),
        headers={},
    )

    async def _body() -> bytes:
        return json.dumps(body).encode()

    request.body = _body
    return request


@pytest.mark.asyncio
async def test_quota_guard_buckets_a_bearer_caller_on_the_key():
    captured: dict = {}

    class _Quota:
        async def check_quota(self, **kwargs):
            captured.update(kwargs)
            return True

    request = _quota_request({"service_name": "ollama", "model": "qwen3:8b"})

    await llm_quota_guard(
        request=request,
        response=Response(),
        x_api_key=None,
        authorization="Bearer eg_bearer",
        llm_quota_service=_Quota(),
    )

    assert captured["api_key"] == "eg_bearer"
    assert captured["bucket_id"] == "eg_bearer"
    assert request.state.api_key == "eg_bearer"
    assert request.state.bucket_id == "eg_bearer"
