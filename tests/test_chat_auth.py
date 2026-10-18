"""Chat endpoint authentication tests.

`POST /api/v1/chat/completions` runs two guards: optional API key auth, then the
quota guard. The contract these tests pin:

* No `X-API-Key` -> allowed, as the ``anonymous`` tenant. Anonymous chat is an
  intentional feature.
* An `X-API-Key` that does not resolve -> ``401``. Presenting an unusable
  credential is an error, never a fallback to anonymous.
* A valid key -> accepted, and the request is attributed to that key's tenant.

The guard is exercised directly rather than through the route so the tests never
reach a provider or touch Redis.
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.core.guards import (
    ApiKeyAuthGuard,
    api_key_auth_guard,
    api_key_auth_optional_guard,
)


def _request():
    request = MagicMock(spec=["state", "headers"])
    request.headers.get.return_value = None
    request.state = MagicMock(spec=["api_key", "tenant_id", "user_id"])
    return request


def _service(identity):
    service = MagicMock()

    async def fake_check_quota(**_kwargs):
        return True

    service.check_quota = fake_check_quota
    return service


def _run_guard(guard, *, x_api_key, resolve_identity=None):
    """Invoke an ApiKeyAuthGuard, optionally stubbing key resolution."""
    if resolve_identity is None:
        return asyncio.run(guard(request=_request(), x_api_key=x_api_key))

    import app.modules.api_keys.api_keys_service as svc

    original = svc.api_key_service.resolve_api_key
    svc.api_key_service.resolve_api_key = lambda _k: resolve_identity
    try:
        return asyncio.run(guard(request=_request(), x_api_key=x_api_key))
    finally:
        svc.api_key_service.resolve_api_key = original


# --- Module-level guard instances --------------------------------------------


def test_strict_guard_instance_rejects_anonymous():
    assert api_key_auth_guard._allow_anonymous is False


def test_optional_guard_instance_allows_anonymous():
    assert api_key_auth_optional_guard._allow_anonymous is True


# --- Strict mode (analytics, gateway-requests) -------------------------------


def test_strict_guard_rejects_missing_key():
    with pytest.raises(HTTPException) as exc:
        _run_guard(api_key_auth_guard, x_api_key=None)
    assert exc.value.status_code == 401
    assert "API Key missing" in exc.value.detail


def test_strict_guard_rejects_unresolvable_key():
    with pytest.raises(HTTPException) as exc:
        _run_guard(api_key_auth_guard, x_api_key="eg_nope", resolve_identity=None)
    assert exc.value.status_code == 401
    assert "Invalid or inactive" in exc.value.detail


# --- Optional mode (chat) ----------------------------------------------------


def test_optional_guard_allows_anonymous_request():
    assert _run_guard(api_key_auth_optional_guard, x_api_key=None) is True


def test_optional_guard_still_rejects_an_unresolvable_key():
    """Supplying a bad credential must fail, not silently become anonymous."""
    with pytest.raises(HTTPException) as exc:
        _run_guard(api_key_auth_optional_guard, x_api_key="eg_nope")
    assert exc.value.status_code == 401
    assert "Invalid or inactive" in exc.value.detail


def test_optional_guard_accepts_and_attributes_a_valid_key():
    from app.modules.api_keys.api_keys_service import ApiKeyIdentity

    identity = ApiKeyIdentity(api_key_id=5, tenant_id="acme", user_id="dev")

    guard = ApiKeyAuthGuard(allow_anonymous=True)
    request = _request()
    import app.modules.api_keys.api_keys_service as svc

    original = svc.api_key_service.resolve_api_key
    svc.api_key_service.resolve_api_key = lambda _k: identity
    try:
        result = asyncio.run(guard(request=request, x_api_key="eg_valid"))
    finally:
        svc.api_key_service.resolve_api_key = original

    assert result is True
    assert request.state.api_key == "eg_valid"
    assert request.state.tenant_id == "acme"
    assert request.state.user_id == "dev"


def test_optional_guard_does_not_claim_an_identity_for_anonymous():
    """An anonymous pass-through must not write identity onto the request.

    Uses a plain object for ``state`` (a MagicMock would auto-create any
    attribute, so an unset key could never be observed as absent).
    """

    class _State:
        api_key = None
        tenant_id = None
        user_id = None

    request = MagicMock(spec=["state", "headers"])
    request.headers.get.return_value = None
    request.state = _State()

    asyncio.run(ApiKeyAuthGuard(allow_anonymous=True)(request=request, x_api_key=None))
    assert request.state.api_key is None
    assert request.state.tenant_id is None
    assert request.state.user_id is None
