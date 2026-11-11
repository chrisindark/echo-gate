from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.guards import api_key_auth_guard
from app.main import app
from app.modules.shadow_cache.shadow_cache_schema import (
    ReadinessStatus,
    ShadowCacheEvaluationResponse,
    ShadowCacheSummaryResponse,
    ShadowMatchType,
    ShadowReadiness,
)
from app.modules.shadow_cache.shadow_cache_service import ShadowCacheService


@pytest.fixture
def mock_shadow_service():
    return MagicMock(spec=ShadowCacheService)


def test_shadow_cache_controller_summary(mock_shadow_service):
    app.dependency_overrides[api_key_auth_guard] = lambda: None
    app.dependency_overrides[get_db] = lambda: MagicMock()

    now = datetime.now(UTC)
    summary_data = ShadowCacheSummaryResponse(
        cache_mode="shadow",
        start_time=now,
        end_time=now,
        time_range="1h",
        total_evaluations=10,
        would_have_hits=5,
        hit_rate=0.5,
        match_type_breakdown=[],
        compared_evaluations=4,
        equivalent_evaluations=4,
        non_equivalent_evaluations=0,
        equivalence_rate=1.0,
        by_intent=[],
        by_model=[],
        readiness=ShadowReadiness(
            status=ReadinessStatus.READY,
            min_evaluations=10,
            min_hit_rate=0.3,
            min_equivalence_rate=0.8,
        ),
    )
    mock_shadow_service.get_summary.return_value = summary_data

    with patch(
        "app.modules.shadow_cache.shadow_cache_controller.ShadowCacheService",
        return_value=mock_shadow_service,
    ):
        client = TestClient(app)

        # Success with time_range
        resp = client.get("/api/v1/shadow-cache/summary?time_range=1h&tenant_id=t1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["response"]["total_evaluations"] == 10

        # Exception branch
        mock_shadow_service.get_summary.side_effect = RuntimeError("db error")
        with pytest.raises(RuntimeError):
            client.get("/api/v1/shadow-cache/summary?time_range=1h")

    app.dependency_overrides.clear()


def test_shadow_cache_controller_evaluations(mock_shadow_service):
    app.dependency_overrides[api_key_auth_guard] = lambda: None
    app.dependency_overrides[get_db] = lambda: MagicMock()

    now = datetime.now(UTC)
    eval_item = ShadowCacheEvaluationResponse(
        id=1,
        created_at=now,
        query_text="What is AI?",
        tenant_id="t1",
        model="gpt-4o",
        intent="general",
        would_have_hit=True,
        match_type=ShadowMatchType.QDRANT_SEMANTIC,
        is_equivalent=True,
        cached_response_text="AI is...",
        actual_response_text="AI is artificial intelligence...",
    )
    mock_shadow_service.get_evaluations.return_value = ([eval_item], 1)

    with patch(
        "app.modules.shadow_cache.shadow_cache_controller.ShadowCacheService",
        return_value=mock_shadow_service,
    ):
        client = TestClient(app)

        # Success with filters
        resp = client.get(
            "/api/v1/shadow-cache/evaluations?skip=0&limit=10&tenant_id=t1&would_have_hit=true&match_type=QDRANT_SEMANTIC&is_equivalent=true&model=gpt-4o&intent=general"
        )
        assert resp.status_code == 200
        data = resp.json()["response"]
        assert data["total"] == 1
        assert len(data["items"]) == 1

        # Exception branch
        mock_shadow_service.get_evaluations.side_effect = RuntimeError("query failure")
        with pytest.raises(RuntimeError):
            client.get("/api/v1/shadow-cache/evaluations")

    app.dependency_overrides.clear()
