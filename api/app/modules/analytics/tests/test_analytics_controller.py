from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.guards import api_key_auth_guard
from app.main import app
from app.modules.analytics.analytics_schema import (
    AnalyticsDashboardResponse,
    CachePerformanceResponse,
    EvaluationSummaryResponse,
    KpiSummaryResponse,
    LatencyComponentBreakdown,
    LatencyMetricsResponse,
    LatencyPercentiles,
)
from app.modules.analytics.analytics_service import AnalyticsService


@pytest.fixture
def mock_analytics_service():
    return MagicMock(spec=AnalyticsService)


def test_analytics_controller_dashboard(mock_analytics_service):
    app.dependency_overrides[api_key_auth_guard] = lambda: None
    app.dependency_overrides[get_db] = lambda: MagicMock()

    mock_dashboard = AnalyticsDashboardResponse(
        kpis=KpiSummaryResponse(
            total_requests=100,
            total_cost=1.5,
            total_tokens=2500,
            avg_latency_ms=150.0,
            cache_hit_rate=0.4,
        ),
        time_series=[],
        cache_performance=CachePerformanceResponse(
            llm_calls=60,
            redis_exact=20,
            qdrant_exact=10,
            qdrant_semantic=10,
            total=100,
            cache_hit_percentage=40.0,
        ),
        model_breakdown=[],
        evaluations=EvaluationSummaryResponse(
            avg_relevance_score=4.5,
            avg_contradiction_score=0.1,
            avg_entailment_score=4.2,
            false_positive_rate=0.05,
            evaluated_count=50,
        ),
        top_intents=[],
        latency_metrics=LatencyMetricsResponse(
            total_requests=100,
            avg_latency_ms=150.0,
            percentiles=LatencyPercentiles(p50=120.0, p90=250.0, p95=300.0, p99=500.0),
            component_breakdown=LatencyComponentBreakdown(),
            by_routing_decision=[],
            time_series=[],
        ),
    )
    mock_analytics_service.get_dashboard_data.return_value = mock_dashboard

    with patch(
        "app.modules.analytics.analytics_controller.AnalyticsService",
        return_value=mock_analytics_service,
    ):
        client = TestClient(app)

        # GET /dashboard success
        resp = client.get("/api/v1/analytics/dashboard?time_range=1h&tenant_id=t1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["response"]["kpis"]["total_requests"] == 100

        # Exception branch
        mock_analytics_service.get_dashboard_data.side_effect = RuntimeError("db crash")
        with pytest.raises(RuntimeError):
            client.get("/api/v1/analytics/dashboard")

    app.dependency_overrides.clear()


def test_analytics_controller_latency(mock_analytics_service):
    app.dependency_overrides[api_key_auth_guard] = lambda: None
    app.dependency_overrides[get_db] = lambda: MagicMock()

    mock_latency = LatencyMetricsResponse(
        total_requests=50,
        avg_latency_ms=100.0,
        percentiles=LatencyPercentiles(p50=80.0, p90=150.0, p95=200.0, p99=300.0),
        component_breakdown=LatencyComponentBreakdown(
            redis_exact_ms=5.0,
            rerank_ms=25.0,
            provider_ms=120.0,
        ),
        by_routing_decision=[],
        time_series=[],
    )
    mock_analytics_service.get_latency_metrics.return_value = mock_latency

    with patch(
        "app.modules.analytics.analytics_controller.AnalyticsService",
        return_value=mock_analytics_service,
    ):
        client = TestClient(app)

        # GET /latency success
        resp = client.get("/api/v1/analytics/latency?time_range=7d&tenant_id=t2")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["response"]["total_requests"] == 50

        # Exception branch
        mock_analytics_service.get_latency_metrics.side_effect = RuntimeError(
            "latency error"
        )
        with pytest.raises(RuntimeError):
            client.get("/api/v1/analytics/latency")

    app.dependency_overrides.clear()
