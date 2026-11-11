"""Analytics aggregation correctness.

The dashboard is a read-only roll-up over ``gateway_requests`` and
``llm_usage_logs``. These tests pin the arithmetic that operators act on —
hit rate, cost/token totals, percentiles, evaluation averages — plus tenant
scoping, so a query change cannot silently shift the numbers.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.analytics.analytics_service import AnalyticsService
from app.modules.gateway_requests.gateway_requests_model import GatewayRequestLog
from app.modules.llm_usage.llm_usage_model import LlmUsageLog

START = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
END = datetime(2026, 1, 1, 11, 0, tzinfo=UTC)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = factory()
    try:
        yield db
    finally:
        db.close()


def _seed(db):
    """Three gateway requests (one LLM, two cache hits) and two usage rows."""
    db.add_all(
        [
            GatewayRequestLog(
                query_text="live call",
                routing_decision="LLM",
                intent="weather",
                latency_ms=100,
                provider_latency_ms=80,
                cache_lookup_latency_ms=12,
                latency_breakdown={"provider_ms": 80, "cache_lookup_total_ms": 12},
                tenant_id="acme",
                created_at=datetime(2026, 1, 1, 10, 5, tzinfo=UTC),
                evaluation_status="EVALUATED",
                llm_relevance_score=0.8,
                llm_contradiction_score=0.1,
                llm_entailment_score=0.9,
                is_false_positive=True,
            ),
            GatewayRequestLog(
                query_text="redis hit",
                routing_decision="REDIS_EXACT",
                intent="general_query",
                latency_ms=20,
                cache_lookup_latency_ms=3,
                latency_breakdown={"cache_lookup_total_ms": 3, "redis_exact_ms": 3},
                tenant_id="acme",
                created_at=datetime(2026, 1, 1, 10, 20, tzinfo=UTC),
                evaluation_status="SKIPPED",
            ),
            GatewayRequestLog(
                query_text="semantic hit",
                routing_decision="QDRANT_SEMANTIC",
                intent="general_query",
                latency_ms=40,
                tenant_id="other",
                created_at=datetime(2026, 1, 1, 10, 40, tzinfo=UTC),
                evaluation_status="PENDING",
            ),
        ]
    )
    db.add_all(
        [
            LlmUsageLog(
                service_name="openai",
                model="gpt-4o-mini",
                caller_service_name="tests",
                tenant_id="acme",
                total_tokens=100,
                cost=0.5,
                created_at=datetime(2026, 1, 1, 10, 10, tzinfo=UTC),
            ),
            LlmUsageLog(
                service_name="openai",
                model="gpt-4o-mini",
                caller_service_name="tests",
                tenant_id="acme",
                total_tokens=50,
                cost=0.25,
                created_at=datetime(2026, 1, 1, 10, 30, tzinfo=UTC),
            ),
            LlmUsageLog(
                service_name="gemini",
                model="gemini-2.0-flash",
                caller_service_name="tests",
                tenant_id="other",
                total_tokens=10,
                cost=1.0,
                created_at=datetime(2026, 1, 1, 10, 45, tzinfo=UTC),
            ),
        ]
    )
    db.commit()


def test_kpis_and_hit_rate(session):
    _seed(session)
    data = AnalyticsService(session).get_dashboard_data(start_time=START, end_time=END)

    assert data.kpis.total_requests == 3
    assert data.kpis.total_tokens == 160
    assert data.kpis.total_cost == pytest.approx(1.75)
    assert data.kpis.avg_latency_ms == pytest.approx(53.33, abs=0.01)
    # 2 of 3 requests were answered from cache.
    assert data.kpis.cache_hit_rate == pytest.approx(66.67, abs=0.01)


def test_cache_performance_splits_by_layer(session):
    _seed(session)
    perf = (
        AnalyticsService(session)
        .get_dashboard_data(start_time=START, end_time=END)
        .cache_performance
    )

    assert perf.llm_calls == 1
    assert perf.redis_exact == 1
    assert perf.qdrant_semantic == 1
    assert perf.qdrant_exact == 0
    assert perf.total == 3
    assert perf.cache_hit_percentage == pytest.approx(66.67, abs=0.01)
    # Cache hits and LLM calls must partition the total exactly.
    assert (
        perf.llm_calls + perf.redis_exact + perf.qdrant_exact + perf.qdrant_semantic
        == perf.total
    )


def test_model_breakdown_groups_by_model_and_provider(session):
    _seed(session)
    rows = (
        AnalyticsService(session)
        .get_dashboard_data(start_time=START, end_time=END)
        .model_breakdown
    )

    by_model = {row.model: row for row in rows}
    assert set(by_model) == {"gpt-4o-mini", "gemini-2.0-flash"}
    assert by_model["gpt-4o-mini"].provider == "openai"
    assert by_model["gpt-4o-mini"].requests == 2
    assert by_model["gpt-4o-mini"].total_tokens == 150
    assert by_model["gpt-4o-mini"].total_cost == pytest.approx(0.75)
    assert by_model["gemini-2.0-flash"].provider == "gemini"


def test_time_series_buckets_sum_to_totals(session):
    _seed(session)
    points = (
        AnalyticsService(session)
        .get_dashboard_data(start_time=START, end_time=END)
        .time_series
    )

    # A one-hour window is bucketed in five-minute steps.
    assert len(points) == 12
    assert sum(p.requests for p in points) == 3
    assert sum(p.tokens for p in points) == 160
    assert sum(p.cost for p in points) == pytest.approx(1.75)


def test_time_series_buckets_carry_cache_hits(session):
    """Each bucket must expose hits, so hit rate over time needs no second call."""
    _seed(session)
    data = AnalyticsService(session).get_dashboard_data(start_time=START, end_time=END)

    points = data.time_series
    assert sum(p.cache_hits for p in points) == 2
    assert sum(p.llm_calls for p in points) == 1
    # Buckets must partition the same way the window-level donut does.
    assert sum(p.cache_hits for p in points) == (
        data.cache_performance.total - data.cache_performance.llm_calls
    )
    assert all(p.cache_hits + p.llm_calls == p.requests for p in points)

    # The seed puts the LLM call at 10:05 and the hits at 10:20 / 10:40.
    by_label = {p.date_label: p for p in points}
    assert by_label["2026-01-01T10:05:00+00:00"].requests == 1
    assert by_label["2026-01-01T10:05:00+00:00"].cache_hits == 0
    assert by_label["2026-01-01T10:20:00+00:00"].cache_hits == 1
    assert by_label["2026-01-01T10:40:00+00:00"].cache_hits == 1


def test_evaluation_summary_only_counts_evaluated_rows(session):
    _seed(session)
    evals = (
        AnalyticsService(session)
        .get_dashboard_data(start_time=START, end_time=END)
        .evaluations
    )

    assert evals.evaluated_count == 1
    assert evals.avg_relevance_score == pytest.approx(0.8)
    assert evals.avg_contradiction_score == pytest.approx(0.1)
    assert evals.avg_entailment_score == pytest.approx(0.9)
    assert evals.false_positive_rate == pytest.approx(100.0)


def test_top_intents_are_ranked(session):
    _seed(session)
    intents = (
        AnalyticsService(session)
        .get_dashboard_data(start_time=START, end_time=END)
        .top_intents
    )

    assert [(i.intent, i.count) for i in intents] == [
        ("general_query", 2),
        ("weather", 1),
    ]


def test_latency_percentiles_and_component_breakdown(session):
    _seed(session)
    latency = (
        AnalyticsService(session)
        .get_dashboard_data(start_time=START, end_time=END)
        .latency_metrics
    )

    assert latency.total_requests == 3
    assert latency.avg_latency_ms == pytest.approx(53.33, abs=0.01)
    # Interpolated over the sorted samples [20, 40, 100].
    assert latency.percentiles.p50 == pytest.approx(40.0)
    assert latency.percentiles.p90 == pytest.approx(88.0)
    assert latency.percentiles.p95 == pytest.approx(94.0)
    assert latency.percentiles.p99 == pytest.approx(98.8)

    assert latency.component_breakdown.redis_exact_ms == pytest.approx(3.0)
    assert latency.component_breakdown.provider_ms == pytest.approx(80.0)
    # cache_lookup_total_ms averages the two rows that reported it: (12 + 3) / 2.
    assert latency.component_breakdown.cache_lookup_total_ms == pytest.approx(7.5)

    by_decision = {row.routing_decision: row for row in latency.by_routing_decision}
    assert by_decision["LLM"].count == 1
    assert by_decision["LLM"].avg_latency_ms == pytest.approx(100.0)
    assert by_decision["REDIS_EXACT"].avg_latency_ms == pytest.approx(20.0)


def test_tenant_scope_excludes_other_tenants(session):
    _seed(session)
    data = AnalyticsService(session).get_dashboard_data(
        start_time=START, end_time=END, tenant_id="acme"
    )

    assert data.kpis.total_requests == 2
    assert data.kpis.total_tokens == 150
    assert data.kpis.total_cost == pytest.approx(0.75)
    assert data.cache_performance.total == 2
    assert data.cache_performance.llm_calls == 1
    assert {row.model for row in data.model_breakdown} == {"gpt-4o-mini"}
    assert sum(p.requests for p in data.time_series) == 2


def test_empty_window_returns_zeros_not_errors(session):
    data = AnalyticsService(session).get_dashboard_data(start_time=START, end_time=END)

    assert data.kpis.total_requests == 0
    assert data.kpis.cache_hit_rate == 0.0
    assert data.kpis.avg_latency_ms == 0.0
    assert data.cache_performance.total == 0
    assert data.cache_performance.cache_hit_percentage == 0.0
    assert data.model_breakdown == []
    assert data.top_intents == []
    assert data.evaluations.evaluated_count == 0
    assert data.evaluations.false_positive_rate == 0.0
    assert data.latency_metrics.total_requests == 0
    assert data.latency_metrics.avg_latency_ms == 0.0
    assert data.latency_metrics.percentiles.p50 is None
    assert all(point.requests == 0 for point in data.time_series)


def test_multiday_time_series_aggregation(session):
    start_7d = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    end_7d = datetime(2026, 1, 8, 0, 0, tzinfo=UTC)

    session.add(
        GatewayRequestLog(
            query_text="multiday query",
            routing_decision="REDIS_EXACT",
            created_at=datetime(2026, 1, 3, 12, 0, tzinfo=UTC),
        )
    )
    session.add(
        LlmUsageLog(
            service_name="openai",
            model="gpt-4o-mini",
            caller_service_name="chat",
            cost=0.05,
            total_tokens=100,
            created_at=datetime(2026, 1, 3, 12, 0, tzinfo=UTC),
        )
    )
    session.commit()

    service = AnalyticsService(session)
    data = service.get_dashboard_data(start_time=start_7d, end_time=end_7d)
    assert len(data.time_series) == 8  # 8 days inclusive
    day_with_data = next(
        (p for p in data.time_series if p.date_label == "2026-01-03"), None
    )
    assert day_with_data is not None
    assert day_with_data.requests == 1
    assert day_with_data.cost == 0.05
    assert day_with_data.cache_hits == 1


def test_latency_metrics_duration_buckets(session):
    from datetime import timedelta

    _seed(session)
    service = AnalyticsService(session)

    # Test each duration branch
    base = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    durations = [
        timedelta(hours=2),  # <= 3h
        timedelta(hours=5),  # <= 6h
        timedelta(hours=10),  # <= 12h
        timedelta(hours=20),  # <= 1d
        timedelta(days=2),  # <= 3d
        timedelta(days=5),  # <= 7d
        timedelta(days=10),  # <= 14d
        timedelta(days=20),  # > 14d
    ]
    for dur in durations:
        lat = service.get_latency_metrics(start_time=base, end_time=base + dur)
        assert lat.total_requests >= 0
        assert len(lat.time_series) > 0
