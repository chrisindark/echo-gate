import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.modules.analytics.analytics_schema import (
    AnalyticsDashboardResponse,
    CachePerformanceResponse,
    EvaluationSummaryResponse,
    KpiSummaryResponse,
    ModelBreakdownItem,
    TimeSeriesDataPoint,
    TopIntentItem,
)
from app.modules.gateway_requests.gateway_requests_model import GatewayRequestLog
from app.modules.llm_usage.llm_usage_model import LlmUsageLog

logger = logging.getLogger(__name__)


class AnalyticsService:
    def __init__(self, db_session_read: Session):
        self.db_session_read = db_session_read

    def get_dashboard_data(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        tenant_id: str | None = None,
    ) -> AnalyticsDashboardResponse:
        # Default to last 30 days if no time provided
        if not end_time:
            end_time = datetime.now(timezone.utc)
        if not start_time:
            start_time = end_time - timedelta(days=30)

        # Ensure timezone-awareness for comparisons if start_time/end_time are naive
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        # 1. KPIs
        kpis = self._calculate_kpis(start_time, end_time, tenant_id)

        # 2. Cache Performance
        cache_perf = self._calculate_cache_performance(start_time, end_time, tenant_id)

        # 3. Model Breakdown
        model_breakdown = self._calculate_model_breakdown(
            start_time, end_time, tenant_id
        )

        # 4. Time Series (Daily)
        time_series = self._calculate_time_series(start_time, end_time, tenant_id)

        # 5. Evaluations Summary
        evaluations = self._calculate_evaluations(start_time, end_time, tenant_id)

        # 6. Top Intents
        top_intents = self._calculate_top_intents(start_time, end_time, tenant_id)

        return AnalyticsDashboardResponse(
            kpis=kpis,
            time_series=time_series,
            cache_performance=cache_perf,
            model_breakdown=model_breakdown,
            evaluations=evaluations,
            top_intents=top_intents,
        )

    def _calculate_kpis(
        self, start_time: datetime, end_time: datetime, tenant_id: str | None
    ) -> KpiSummaryResponse:
        # LLM Usage Totals (Cost, Tokens)
        llm_query = self.db_session_read.query(
            func.sum(LlmUsageLog.cost).label("total_cost"),
            func.sum(LlmUsageLog.total_tokens).label("total_tokens"),
        ).filter(
            LlmUsageLog.created_at >= start_time,
            LlmUsageLog.created_at <= end_time,
        )
        if tenant_id:
            llm_query = llm_query.filter(LlmUsageLog.tenant_id == tenant_id)

        llm_stats = llm_query.first()
        total_cost = float(llm_stats.total_cost or 0.0)
        total_tokens = int(llm_stats.total_tokens or 0)

        # Gateway Totals (Requests, Latency, Cache Hits)
        gw_query = self.db_session_read.query(
            func.count(GatewayRequestLog.id).label("total_requests"),
            func.avg(GatewayRequestLog.latency_ms).label("avg_latency"),
            func.sum(
                case((GatewayRequestLog.routing_decision != "LLM", 1), else_=0)
            ).label("cache_hits"),
        ).filter(
            GatewayRequestLog.created_at >= start_time,
            GatewayRequestLog.created_at <= end_time,
        )
        if tenant_id:
            gw_query = gw_query.filter(GatewayRequestLog.tenant_id == tenant_id)

        gw_stats = gw_query.first()
        total_requests = int(gw_stats.total_requests or 0)
        avg_latency = float(gw_stats.avg_latency or 0.0)
        cache_hits = int(gw_stats.cache_hits or 0)

        hit_rate = (cache_hits / total_requests * 100.0) if total_requests > 0 else 0.0

        return KpiSummaryResponse(
            total_requests=total_requests,
            total_cost=total_cost,
            total_tokens=total_tokens,
            avg_latency_ms=round(avg_latency, 2),
            cache_hit_rate=round(hit_rate, 2),
        )

    def _calculate_cache_performance(
        self, start_time: datetime, end_time: datetime, tenant_id: str | None
    ) -> CachePerformanceResponse:
        query = self.db_session_read.query(
            GatewayRequestLog.routing_decision,
            func.count(GatewayRequestLog.id).label("count"),
        ).filter(
            GatewayRequestLog.created_at >= start_time,
            GatewayRequestLog.created_at <= end_time,
        )
        if tenant_id:
            query = query.filter(GatewayRequestLog.tenant_id == tenant_id)

        results = query.group_by(GatewayRequestLog.routing_decision).all()

        counts = defaultdict(int)
        total = 0
        for row in results:
            counts[row.routing_decision] = row.count
            total += row.count

        llm_calls = counts.get("LLM", 0)
        cache_hits = total - llm_calls
        hit_rate = (cache_hits / total * 100.0) if total > 0 else 0.0

        return CachePerformanceResponse(
            llm_calls=llm_calls,
            redis_exact=counts.get("REDIS_EXACT", 0),
            qdrant_exact=counts.get("QDRANT_EXACT", 0),
            qdrant_semantic=counts.get("QDRANT_SEMANTIC", 0),
            total=total,
            cache_hit_percentage=round(hit_rate, 2),
        )

    def _calculate_model_breakdown(
        self, start_time: datetime, end_time: datetime, tenant_id: str | None
    ) -> list[ModelBreakdownItem]:
        query = self.db_session_read.query(
            LlmUsageLog.model,
            LlmUsageLog.service_name.label("provider"),
            func.count(LlmUsageLog.id).label("requests"),
            func.sum(LlmUsageLog.cost).label("total_cost"),
            func.sum(LlmUsageLog.total_tokens).label("total_tokens"),
        ).filter(
            LlmUsageLog.created_at >= start_time,
            LlmUsageLog.created_at <= end_time,
        )

        if tenant_id:
            query = query.filter(LlmUsageLog.tenant_id == tenant_id)

        results = query.group_by(LlmUsageLog.model, LlmUsageLog.service_name).all()

        return [
            ModelBreakdownItem(
                model=row.model or "unknown",
                provider=row.provider or "unknown",
                requests=row.requests or 0,
                total_cost=float(row.total_cost or 0.0),
                total_tokens=int(row.total_tokens or 0),
            )
            for row in results
        ]

    def _calculate_time_series(
        self, start_time: datetime, end_time: datetime, tenant_id: str | None
    ) -> list[TimeSeriesDataPoint]:
        # Fetching raw fields to do an in-memory group by day to avoid issues with Date casting
        # across different SQL dialects (SQLite vs Postgres).
        gw_query = self.db_session_read.query(GatewayRequestLog.created_at).filter(
            GatewayRequestLog.created_at >= start_time,
            GatewayRequestLog.created_at <= end_time,
        )
        if tenant_id:
            gw_query = gw_query.filter(GatewayRequestLog.tenant_id == tenant_id)

        llm_query = self.db_session_read.query(
            LlmUsageLog.created_at, LlmUsageLog.cost, LlmUsageLog.total_tokens
        ).filter(
            LlmUsageLog.created_at >= start_time,
            LlmUsageLog.created_at <= end_time,
        )
        if tenant_id:
            llm_query = llm_query.filter(LlmUsageLog.tenant_id == tenant_id)

        daily_stats = defaultdict(lambda: {"requests": 0, "cost": 0.0, "tokens": 0})

        for row in gw_query.all():
            if row.created_at:
                day_str = row.created_at.strftime("%Y-%m-%d")
                daily_stats[day_str]["requests"] += 1

        for row in llm_query.all():
            if row.created_at:
                day_str = row.created_at.strftime("%Y-%m-%d")
                daily_stats[day_str]["cost"] += float(row.cost or 0.0)
                daily_stats[day_str]["tokens"] += int(row.total_tokens or 0)

        # Fill in missing dates sequentially
        curr_date = start_time.date()
        end_date = end_time.date()
        time_series = []

        while curr_date <= end_date:
            day_str = curr_date.strftime("%Y-%m-%d")
            stats = daily_stats[day_str]
            time_series.append(
                TimeSeriesDataPoint(
                    date_label=day_str,
                    requests=stats["requests"],
                    cost=round(stats["cost"], 6),
                    tokens=stats["tokens"],
                )
            )
            curr_date += timedelta(days=1)

        return time_series

    def _calculate_evaluations(
        self, start_time: datetime, end_time: datetime, tenant_id: str | None
    ) -> EvaluationSummaryResponse:
        query = self.db_session_read.query(
            func.avg(GatewayRequestLog.llm_relevance_score).label("avg_relevance"),
            func.avg(GatewayRequestLog.llm_contradiction_score).label(
                "avg_contradiction"
            ),
            func.avg(GatewayRequestLog.llm_instruction_score).label("avg_instruction"),
            func.sum(
                case((GatewayRequestLog.is_false_positive.is_(True), 1), else_=0)
            ).label("false_positives"),
            func.count(GatewayRequestLog.id).label("evaluated_count"),
        ).filter(
            GatewayRequestLog.created_at >= start_time,
            GatewayRequestLog.created_at <= end_time,
            GatewayRequestLog.evaluation_status == "EVALUATED",
        )
        if tenant_id:
            query = query.filter(GatewayRequestLog.tenant_id == tenant_id)

        stats = query.first()
        evaluated_count = int(stats.evaluated_count or 0)
        fp_rate = (
            (int(stats.false_positives or 0) / evaluated_count * 100.0)
            if evaluated_count > 0
            else 0.0
        )

        return EvaluationSummaryResponse(
            avg_relevance_score=round(float(stats.avg_relevance), 2)
            if stats.avg_relevance
            else None,
            avg_contradiction_score=round(float(stats.avg_contradiction), 2)
            if stats.avg_contradiction
            else None,
            avg_instruction_score=round(float(stats.avg_instruction), 2)
            if stats.avg_instruction
            else None,
            false_positive_rate=round(fp_rate, 2),
            evaluated_count=evaluated_count,
        )

    def _calculate_top_intents(
        self, start_time: datetime, end_time: datetime, tenant_id: str | None
    ) -> list[TopIntentItem]:
        query = self.db_session_read.query(
            GatewayRequestLog.intent, func.count(GatewayRequestLog.id).label("count")
        ).filter(
            GatewayRequestLog.created_at >= start_time,
            GatewayRequestLog.created_at <= end_time,
            GatewayRequestLog.intent.isnot(None),
        )
        if tenant_id:
            query = query.filter(GatewayRequestLog.tenant_id == tenant_id)

        results = (
            query.group_by(GatewayRequestLog.intent)
            .order_by(func.count(GatewayRequestLog.id).desc())
            .limit(10)
            .all()
        )

        return [TopIntentItem(intent=row.intent, count=row.count) for row in results]
