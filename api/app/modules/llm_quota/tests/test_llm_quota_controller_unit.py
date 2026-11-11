from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.dependencies import get_llm_quota_rule_service
from app.main import app
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.llm_quota.llm_quota_controller import (
    RequireQuotaRead,
    RequireQuotaWrite,
)
from app.modules.llm_quota.llm_quota_model import LlmQuotaRule
from app.modules.llm_quota.llm_quota_rule_service import LlmQuotaRuleService


@pytest.fixture
def mock_admin():
    return AdminKeyIdentity(
        key_id=1, name="admin", scopes=frozenset({"quota:read", "quota:write"})
    )


@pytest.fixture
def mock_quota_service():
    return MagicMock(spec=LlmQuotaRuleService)


def _make_rule(id: int = 1, tenant_id: str | None = "tenant_1"):
    now = datetime.now(UTC)
    return LlmQuotaRule(
        id=id,
        tenant_id=tenant_id,
        max_rpm=60,
        max_tpm=10000,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def test_llm_quota_controller_create_rule(mock_admin, mock_quota_service):
    app.dependency_overrides[RequireQuotaWrite] = lambda: mock_admin
    app.dependency_overrides[get_llm_quota_rule_service] = lambda: mock_quota_service

    client = TestClient(app)

    rule = _make_rule()
    mock_quota_service.create_rule.return_value = rule

    # Success (201)
    resp = client.post(
        "/api/v1/admin/llm-quota/", json={"tenant_id": "tenant_1", "max_rpm": 60}
    )
    assert resp.status_code == 201
    assert resp.json()["success"] is True

    # ValueError -> 400
    mock_quota_service.create_rule.side_effect = ValueError("Invalid quota parameters")
    resp_400 = client.post(
        "/api/v1/admin/llm-quota/", json={"tenant_id": "tenant_1", "max_rpm": 60}
    )
    assert resp_400.status_code == 400
    assert "Invalid quota parameters" in resp_400.json()["detail"]

    # HTTPException re-raised
    mock_quota_service.create_rule.side_effect = HTTPException(
        status_code=403, detail="Forbidden"
    )
    resp_403 = client.post(
        "/api/v1/admin/llm-quota/", json={"tenant_id": "tenant_1", "max_rpm": 60}
    )
    assert resp_403.status_code == 403

    # Exception -> 500
    mock_quota_service.create_rule.side_effect = RuntimeError("DB connection dropped")
    resp_500 = client.post(
        "/api/v1/admin/llm-quota/", json={"tenant_id": "tenant_1", "max_rpm": 60}
    )
    assert resp_500.status_code == 500

    app.dependency_overrides.clear()


def test_llm_quota_controller_list_rules(mock_admin, mock_quota_service):
    app.dependency_overrides[RequireQuotaRead] = lambda: mock_admin
    app.dependency_overrides[get_llm_quota_rule_service] = lambda: mock_quota_service

    client = TestClient(app)

    mock_quota_service.list_rules.return_value = ([_make_rule(1), _make_rule(2)], 2)

    resp = client.get(
        "/api/v1/admin/llm-quota/?skip=0&limit=10&tenant_id=tenant_1&provider=openai&model=gpt-4o&is_active=true"
    )
    assert resp.status_code == 200
    data = resp.json()["response"]
    assert data["total"] == 2
    assert len(data["rules"]) == 2

    app.dependency_overrides.clear()


def test_llm_quota_controller_list_tenants(mock_admin, mock_quota_service):
    app.dependency_overrides[RequireQuotaRead] = lambda: mock_admin
    app.dependency_overrides[get_llm_quota_rule_service] = lambda: mock_quota_service

    client = TestClient(app)
    mock_quota_service.list_tenants.return_value = ["tenant_a", "tenant_b"]

    resp = client.get("/api/v1/admin/llm-quota/tenants")
    assert resp.status_code == 200
    assert resp.json()["response"] == ["tenant_a", "tenant_b"]

    app.dependency_overrides.clear()


def test_llm_quota_controller_effective_limits(mock_admin, mock_quota_service):
    app.dependency_overrides[RequireQuotaRead] = lambda: mock_admin
    app.dependency_overrides[get_llm_quota_rule_service] = lambda: mock_quota_service

    client = TestClient(app)
    mock_quota_service.preview_effective_limits.return_value = (100, 50000)

    resp = client.get(
        "/api/v1/admin/llm-quota/effective?tenant_id=t1&provider=openai&model=gpt-4o&user=u1"
    )
    assert resp.status_code == 200
    res = resp.json()["response"]
    assert res["max_rpm"] == 100
    assert res["max_tpm"] == 50000
    assert res["uses_defaults"] is False

    app.dependency_overrides.clear()


def test_llm_quota_controller_get_rule(mock_admin, mock_quota_service):
    app.dependency_overrides[RequireQuotaRead] = lambda: mock_admin
    app.dependency_overrides[get_llm_quota_rule_service] = lambda: mock_quota_service

    client = TestClient(app)

    # Found
    mock_quota_service.get_rule.return_value = _make_rule(1)
    resp = client.get("/api/v1/admin/llm-quota/1")
    assert resp.status_code == 200
    assert resp.json()["response"]["id"] == 1

    # Not found
    mock_quota_service.get_rule.return_value = None
    resp_404 = client.get("/api/v1/admin/llm-quota/999")
    assert resp_404.status_code == 404

    app.dependency_overrides.clear()


def test_llm_quota_controller_update_rule(mock_admin, mock_quota_service):
    app.dependency_overrides[RequireQuotaWrite] = lambda: mock_admin
    app.dependency_overrides[get_llm_quota_rule_service] = lambda: mock_quota_service

    client = TestClient(app)

    # Found & updated
    mock_quota_service.update_rule.return_value = _make_rule(1)
    resp = client.patch("/api/v1/admin/llm-quota/1", json={"max_rpm": 120})
    assert resp.status_code == 200

    # Not found
    mock_quota_service.update_rule.return_value = None
    resp_404 = client.patch("/api/v1/admin/llm-quota/999", json={"max_rpm": 120})
    assert resp_404.status_code == 404

    app.dependency_overrides.clear()


def test_llm_quota_controller_delete_rule(mock_admin, mock_quota_service):
    app.dependency_overrides[RequireQuotaWrite] = lambda: mock_admin
    app.dependency_overrides[get_llm_quota_rule_service] = lambda: mock_quota_service

    client = TestClient(app)

    # Deleted
    mock_quota_service.delete_rule.return_value = True
    resp = client.delete("/api/v1/admin/llm-quota/1")
    assert resp.status_code == 200

    # Not found
    mock_quota_service.delete_rule.return_value = False
    resp_404 = client.delete("/api/v1/admin/llm-quota/999")
    assert resp_404.status_code == 404

    app.dependency_overrides.clear()
