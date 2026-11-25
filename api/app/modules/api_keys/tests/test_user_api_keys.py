import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core import supabase_jwt
from app.core.config import config
from app.core.guards import UserAuthGuard
from app.core.identity import (
    ChainedIdentityProvider,
    SupabaseJwtIdentityProvider,
)
from app.modules.api_keys.api_keys_model import ApiKey
from app.modules.api_keys.api_keys_schema import ApiKeyCreate
from app.modules.api_keys.api_keys_service import ApiKeyService, hash_api_key
from app.modules.api_keys.user_api_keys_controller import user_api_v1_router

JWKS_URL = "https://example.supabase.co/auth/v1/.well-known/jwks.json"

_EC_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
_OTHER_EC_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
_PUBLIC_JWK = jwt.PyJWK.from_dict(
    {
        **json.loads(jwt.algorithms.ECAlgorithm.to_jwk(_EC_PRIVATE_KEY.public_key())),
        "kid": "test-kid-1",
        "use": "sig",
        "alg": "ES256",
    }
)


def _make_jwt(
    sub: str = "user-123",
    email: str = "test@example.com",
    role: str = "authenticated",
    aud: str = "authenticated",
    tenant_id: str | None = None,
    expires_in_seconds: int = 3600,
    private_key=_EC_PRIVATE_KEY,
) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": email,
        "role": role,
        "aud": aud,
        "iat": now,
        "exp": now + expires_in_seconds,
    }
    if tenant_id:
        payload["user_metadata"] = {"tenant_id": tenant_id}
    return jwt.encode(
        payload,
        private_key,
        algorithm="ES256",
        headers={"kid": "test-kid-1"},
    )


def _mock_jwks_client():
    client = MagicMock()
    client.get_signing_key_from_jwt.return_value = _PUBLIC_JWK
    return client


# ============================================================================
# 1. SupabaseJwtIdentityProvider Tests
# ============================================================================


def test_supabase_jwt_identity_provider_resolves_valid_token():
    provider = SupabaseJwtIdentityProvider()
    token = _make_jwt(sub="user-456", email="alice@test.com")

    with (
        patch.object(config, "SUPABASE_JWKS_URL", JWKS_URL),
        patch.object(config, "SUPABASE_AUTH_ENABLED", True),
        patch.object(config, "SUPABASE_JWT_ALGORITHMS", ["ES256", "RS256"]),
        patch.object(supabase_jwt, "get_jwk_client", return_value=_mock_jwks_client()),
    ):
        identity = provider.resolve({"authorization": f"Bearer {token}"})
        assert identity is not None
        assert identity.user_id == "user-456"
        assert identity.tenant_id == "usr_user-456"


def test_supabase_jwt_identity_provider_respects_custom_tenant():
    provider = SupabaseJwtIdentityProvider()
    token = _make_jwt(sub="user-789", tenant_id="org_acme")

    with (
        patch.object(config, "SUPABASE_JWKS_URL", JWKS_URL),
        patch.object(config, "SUPABASE_AUTH_ENABLED", True),
        patch.object(config, "SUPABASE_JWT_ALGORITHMS", ["ES256", "RS256"]),
        patch.object(supabase_jwt, "get_jwk_client", return_value=_mock_jwks_client()),
    ):
        identity = provider.resolve({"authorization": f"Bearer {token}"})
        assert identity is not None
        assert identity.user_id == "user-789"
        assert identity.tenant_id == "org_acme"


def test_supabase_jwt_identity_provider_returns_none_when_disabled():
    provider = SupabaseJwtIdentityProvider()
    token = _make_jwt()

    with (
        patch.object(config, "SUPABASE_JWKS_URL", ""),
        patch.object(config, "SUPABASE_URL", ""),
        patch.object(config, "SUPABASE_AUTH_ENABLED", False),
    ):
        assert provider.resolve({"authorization": f"Bearer {token}"}) is None


def test_supabase_jwt_identity_provider_returns_none_on_tampered_token():
    provider = SupabaseJwtIdentityProvider()
    token = _make_jwt(private_key=_OTHER_EC_PRIVATE_KEY)

    with (
        patch.object(config, "SUPABASE_JWKS_URL", JWKS_URL),
        patch.object(config, "SUPABASE_AUTH_ENABLED", True),
        patch.object(config, "SUPABASE_JWT_ALGORITHMS", ["ES256", "RS256"]),
        patch.object(supabase_jwt, "get_jwk_client", return_value=_mock_jwks_client()),
    ):
        assert provider.resolve({"authorization": f"Bearer {token}"}) is None


def test_chained_identity_provider_falls_through():
    token = _make_jwt(sub="user-chain")
    api_key_provider = MagicMock()
    api_key_provider.resolve.return_value = None

    with (
        patch.object(config, "SUPABASE_JWKS_URL", JWKS_URL),
        patch.object(config, "SUPABASE_AUTH_ENABLED", True),
        patch.object(config, "SUPABASE_JWT_ALGORITHMS", ["ES256", "RS256"]),
        patch.object(supabase_jwt, "get_jwk_client", return_value=_mock_jwks_client()),
    ):
        jwt_provider = SupabaseJwtIdentityProvider()
        chain = ChainedIdentityProvider([api_key_provider, jwt_provider])

        identity = chain.resolve({"authorization": f"Bearer {token}"})
        assert identity is not None
        assert identity.user_id == "user-chain"
        assert identity.tenant_id == "usr_user-chain"


# ============================================================================
# 2. UserAuthGuard Unit Tests
# ============================================================================


@pytest.mark.asyncio
async def test_user_auth_guard_rejects_missing_auth():
    guard = UserAuthGuard()
    request = MagicMock()
    with pytest.raises(HTTPException) as exc_info:
        await guard(request=request, authorization=None)
    assert exc_info.value.status_code == 401
    assert "Authentication required" in exc_info.value.detail


@pytest.mark.asyncio
async def test_user_auth_guard_rejects_non_bearer_scheme():
    guard = UserAuthGuard()
    request = MagicMock()
    with pytest.raises(HTTPException) as exc_info:
        await guard(request=request, authorization="Basic 12345")
    assert exc_info.value.status_code == 401
    assert "Invalid authorization scheme" in exc_info.value.detail


@pytest.mark.asyncio
async def test_user_auth_guard_rejects_when_jwks_not_configured():
    guard = UserAuthGuard()
    request = MagicMock()
    token = _make_jwt()

    with (
        patch.object(config, "SUPABASE_JWKS_URL", ""),
        patch.object(config, "SUPABASE_URL", ""),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await guard(request=request, authorization=f"Bearer {token}")
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_user_auth_guard_rejects_expired_token():
    guard = UserAuthGuard()
    request = MagicMock()
    expired_token = _make_jwt(expires_in_seconds=-10)

    with (
        patch.object(config, "SUPABASE_JWKS_URL", JWKS_URL),
        patch.object(config, "SUPABASE_JWT_ALGORITHMS", ["ES256", "RS256"]),
        patch.object(supabase_jwt, "get_jwk_client", return_value=_mock_jwks_client()),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await guard(request=request, authorization=f"Bearer {expired_token}")
        assert exc_info.value.status_code == 401
        assert "Token has expired" in exc_info.value.detail


@pytest.mark.asyncio
async def test_user_auth_guard_rejects_invalid_signature():
    guard = UserAuthGuard()
    request = MagicMock()
    bad_token = _make_jwt(private_key=_OTHER_EC_PRIVATE_KEY)

    with (
        patch.object(config, "SUPABASE_JWKS_URL", JWKS_URL),
        patch.object(config, "SUPABASE_JWT_ALGORITHMS", ["ES256", "RS256"]),
        patch.object(supabase_jwt, "get_jwk_client", return_value=_mock_jwks_client()),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await guard(request=request, authorization=f"Bearer {bad_token}")
        assert exc_info.value.status_code == 401
        assert "Invalid authentication token" in exc_info.value.detail


@pytest.mark.asyncio
async def test_user_auth_guard_succeeds_and_binds_request_state():
    guard = UserAuthGuard()
    request = MagicMock()
    request.state = MagicMock()
    token = _make_jwt(sub="usr-1234", email="bob@test.com")

    with (
        patch.object(config, "SUPABASE_JWKS_URL", JWKS_URL),
        patch.object(config, "SUPABASE_JWT_ALGORITHMS", ["ES256", "RS256"]),
        patch.object(supabase_jwt, "get_jwk_client", return_value=_mock_jwks_client()),
    ):
        identity = await guard(request=request, authorization=f"Bearer {token}")
        assert identity.user_id == "usr-1234"
        assert identity.tenant_id == "usr_usr-1234"
        assert identity.email == "bob@test.com"
        assert request.state.user_id == "usr-1234"
        assert request.state.tenant_id == "usr_usr-1234"


# ============================================================================
# 3. ApiKeyService Tenant-Scoped Isolation Tests
# ============================================================================


def test_api_key_service_tenant_scoped_methods():
    service = ApiKeyService()

    # Create mock session
    mock_session = MagicMock()
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = mock_session
    service._session_factory = mock_session_factory

    key1 = ApiKey(
        id=1,
        name="Key 1",
        key_prefix="eg_1234",
        key_hash="hash1",
        tenant_id="tenant_A",
        user_id="user_A",
        is_active=True,
    )
    key2 = ApiKey(
        id=2,
        name="Key 2",
        key_prefix="eg_5678",
        key_hash="hash2",
        tenant_id="tenant_B",
        user_id="user_B",
        is_active=True,
    )

    # list_api_keys_for_tenant
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [key1]
    mock_session.execute.return_value = mock_result
    keys = service.list_api_keys_for_tenant("tenant_A")
    assert len(keys) == 1
    assert keys[0].tenant_id == "tenant_A"

    # revoke_api_key_for_tenant: unauthorized cross-tenant attempt
    mock_session.get.return_value = key2  # belongs to tenant_B
    revoked = service.revoke_api_key_for_tenant(key_id=2, tenant_id="tenant_A")
    assert revoked is False
    assert key2.is_active is True

    # revoke_api_key_for_tenant: matching tenant
    mock_session.get.return_value = key1
    revoked = service.revoke_api_key_for_tenant(key_id=1, tenant_id="tenant_A")
    assert revoked is True
    assert key1.is_active is False

    # rotate_api_key_for_tenant: unauthorized cross-tenant attempt
    mock_session.get.return_value = key2
    rotated = service.rotate_api_key_for_tenant(key_id=2, tenant_id="tenant_A")
    assert rotated is None


# ============================================================================
# 4. User API Endpoints HTTP Integration Tests
# ============================================================================


@pytest.fixture
def user_test_app():
    test_app = FastAPI()
    test_app.include_router(user_api_v1_router)
    return test_app


def test_user_api_me_endpoint(user_test_app):
    token = _make_jwt(sub="usr-abc", email="abc@example.com")
    with (
        patch.object(config, "SUPABASE_JWKS_URL", JWKS_URL),
        patch.object(config, "SUPABASE_JWT_ALGORITHMS", ["ES256", "RS256"]),
        patch.object(supabase_jwt, "get_jwk_client", return_value=_mock_jwks_client()),
    ):
        client = TestClient(user_test_app)
        res = client.get(
            "/api/v1/user/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 200
        data = res.json()["response"]
        assert data["user_id"] == "usr-abc"
        assert data["tenant_id"] == "usr_usr-abc"
        assert data["email"] == "abc@example.com"


def test_user_api_keys_crud_lifecycle(user_test_app):
    token = _make_jwt(sub="usr-xyz", email="xyz@example.com")
    stored_keys: dict[int, ApiKey] = {}
    current_id = [1]

    mock_service = MagicMock(spec=ApiKeyService)

    def mock_create(data: ApiKeyCreate):
        k_id = current_id[0]
        current_id[0] += 1
        record = ApiKey(
            id=k_id,
            name=data.name,
            key_prefix="eg_12345678",
            key_hash=hash_api_key(f"eg_test_plaintext_{k_id}"),
            tenant_id=data.tenant_id,
            user_id=data.user_id,
            is_active=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        stored_keys[k_id] = record
        return record, f"eg_test_plaintext_{k_id}"

    def mock_list(tenant_id: str, skip: int = 0, limit: int = 100):
        return [k for k in stored_keys.values() if k.tenant_id == tenant_id]

    def mock_revoke(key_id: int, tenant_id: str):
        if key_id in stored_keys and stored_keys[key_id].tenant_id == tenant_id:
            stored_keys[key_id].is_active = False
            return True
        return False

    def mock_rotate(key_id: int, tenant_id: str, grace_hours: int = 24):
        if (
            key_id in stored_keys
            and stored_keys[key_id].tenant_id == tenant_id
            and stored_keys[key_id].is_active
        ):
            old = stored_keys[key_id]
            old.expires_at = datetime.now(UTC) + timedelta(hours=grace_hours)
            new_k, plaintext = mock_create(
                ApiKeyCreate(
                    name=f"{old.name} (rotated)",
                    tenant_id=tenant_id,
                    user_id=old.user_id,
                )
            )
            return new_k, plaintext, old
        return None

    mock_service.create_api_key.side_effect = mock_create
    mock_service.list_api_keys_for_tenant.side_effect = mock_list
    mock_service.revoke_api_key_for_tenant.side_effect = mock_revoke
    mock_service.rotate_api_key_for_tenant.side_effect = mock_rotate

    from app.core.dependencies import get_api_key_service

    user_test_app.dependency_overrides[get_api_key_service] = lambda: mock_service

    with (
        patch.object(config, "SUPABASE_JWKS_URL", JWKS_URL),
        patch.object(config, "SUPABASE_JWT_ALGORITHMS", ["ES256", "RS256"]),
        patch.object(supabase_jwt, "get_jwk_client", return_value=_mock_jwks_client()),
    ):
        client = TestClient(user_test_app)
        auth_header = {"Authorization": f"Bearer {token}"}

        # 1. Create key
        create_res = client.post(
            "/api/v1/user/api-keys",
            json={"name": "Dev Client Key"},
            headers=auth_header,
        )
        assert create_res.status_code == 201
        created_data = create_res.json()["response"]
        assert created_data["name"] == "Dev Client Key"
        assert created_data["tenant_id"] == "usr_usr-xyz"
        assert created_data["user_id"] == "usr-xyz"
        assert "api_key" in created_data
        assert created_data["api_key"].startswith("eg_")
        key_id = created_data["id"]

        # 2. List keys
        list_res = client.get("/api/v1/user/api-keys", headers=auth_header)
        assert list_res.status_code == 200
        keys_list = list_res.json()["response"]
        assert len(keys_list) == 1
        assert keys_list[0]["id"] == key_id
        # Metadata only; plaintext is not returned in list
        assert "api_key" not in keys_list[0]

        # 3. Rotate key
        rotate_res = client.post(
            f"/api/v1/user/api-keys/{key_id}/rotate",
            json={"grace_hours": 48},
            headers=auth_header,
        )
        assert rotate_res.status_code == 200
        rotate_data = rotate_res.json()["response"]
        assert rotate_data["new_key"]["name"] == "Dev Client Key (rotated)"
        assert rotate_data["new_key"]["api_key"].startswith("eg_")
        assert rotate_data["expiring_key"]["id"] == key_id

        # 4. Revoke key
        revoke_res = client.delete(
            f"/api/v1/user/api-keys/{key_id}", headers=auth_header
        )
        assert revoke_res.status_code == 200

        # Cross-tenant attempt from another user fails
        other_user_token = _make_jwt(sub="usr-other")
        other_auth = {"Authorization": f"Bearer {other_user_token}"}
        cross_res = client.delete(f"/api/v1/user/api-keys/{key_id}", headers=other_auth)
        assert cross_res.status_code == 404
