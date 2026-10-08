import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.modules.analytics.analytics_schema import (
    AnalyticsDashboardResponse,
    CachePerformanceResponse,
    EvaluationSummaryResponse,
    KpiSummaryResponse,
    LatencyByRoutingDecision,
    LatencyComponentBreakdown,
    LatencyMetricsResponse,
    LatencyPercentiles,
    LatencyTimeSeriesPoint,
    ModelBreakdownItem,
    TimeSeriesDataPoint,
    TopIntentItem,
)
from app.modules.gateway_requests.gateway_requests_model import GatewayRequestLog
from app.modules.llm_usage.llm_usage_model import LlmUsageLog

logger = logging.getLogger(__name__)

TIME_RANGE_MAP: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "3h": timedelta(hours=3),
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "1d": timedelta(days=1),
    "24h": timedelta(days=1),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
    "14d": timedelta(days=14),
    "30d": timedelta(days=30),
}


def _calculate_percentile(data: list[float], percentile: float) -> float | None:
    if not data:
        return None
    sorted_data = sorted(data)
    if len(sorted_data) == 1:
        return round(sorted_data[0], 2)
    k = (len(sorted_data) - 1) * (percentile / 100.0)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return round(sorted_data[-1], 2)
    d0 = sorted_data[f] * (c - k)
    d1 = sorted_data[c] * (k - f)
    return round(d0 + d1, 2)


class AnalyticsService:
    def __init__(self, db_session_read: Session):
        self.db_session_read = db_session_read

    def _resolve_time_range(
        self,
        time_range: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[datetime, datetime, str | None]:
        if not end_time:
            end_time = datetime.now(timezone.utc)
        elif end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        resolved_range = time_range.lower() if time_range else None
        if resolved_range in TIME_RANGE_MAP:
            start_time = end_time - TIME_RANGE_MAP[resolved_range]
        elif not start_time:
            start_time = end_time - timedelta(days=7)
            resolved_range = "7d"
        elif start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        return start_time, end_time, resolved_range

    def get_dashboard_data(
        self,
        time_range: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        tenant_id: str | None = None,
    ) -> AnalyticsDashboardResponse:
        start_time, end_time, time_range_label = self._resolve_time_range(
            time_range=time_range, start_time=start_time, end_time=end_time
        )

        # 1. KPIs
        kpis = self._calculate_kpis(start_time, end_time, tenant_id)

        # 2. Cache Performance
        cache_perf = self._calculate_cache_performance(start_time, end_time, tenant_id)

        # 3. Model Breakdown
        model_breakdown = self._calculate_model_breakdown(
            start_time, end_time, tenant_id
        )

        # 4. Time Series
        time_series = self._calculate_time_series(start_time, end_time, tenant_id)

        # 5. Evaluations Summary
        evaluations = self._calculate_evaluations(start_time, end_time, tenant_id)

        # 6. Top Intents
        top_intents = self._calculate_top_intents(start_time, end_time, tenant_id)

        # 7. Latency Metrics
        latency_metrics = self._calculate_latency_metrics(
            start_time, end_time, tenant_id, time_range_label
        )

        return AnalyticsDashboardResponse(
            kpis=kpis,
            time_series=time_series,
            cache_performance=cache_perf,
            model_breakdown=model_breakdown,
            evaluations=evaluations,
            top_intents=top_intents,
            latency_metrics=latency_metrics,
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
        # Fetching raw fields to do an in-memory group to avoid issues with Date casting
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

        duration = end_time - start_time
        if duration <= timedelta(days=1):
            if duration <= timedelta(hours=1):
                step = timedelta(minutes=5)
            elif duration <= timedelta(hours=3):
                step = timedelta(minutes=15)
            elif duration <= timedelta(hours=6):
                step = timedelta(minutes=30)
            elif duration <= timedelta(hours=12):
                step = timedelta(hours=1)
            else:
                step = timedelta(hours=2)

            buckets = []
            curr = start_time
            while curr < end_time:
                next_curr = min(curr + step, end_time)
                buckets.append(
                    {
                        "start": curr,
                        "end": next_curr,
                        "label": curr.isoformat(),
                        "requests": 0,
                        "cost": 0.0,
                        "tokens": 0,
                    }
                )
                curr = next_curr

            for row in gw_query.all():
                if row.created_at:
                    cat = (
                        row.created_at.replace(tzinfo=timezone.utc)
                        if row.created_at.tzinfo is None
                        else row.created_at
                    )
                    for b in buckets:
                        if b["start"] <= cat < b["end"]:
                            b["requests"] += 1
                            break

            for row in llm_query.all():
                if row.created_at:
                    cat = (
                        row.created_at.replace(tzinfo=timezone.utc)
                        if row.created_at.tzinfo is None
                        else row.created_at
                    )
                    for b in buckets:
                        if b["start"] <= cat < b["end"]:
                            b["cost"] += float(row.cost or 0.0)
                            b["tokens"] += int(row.total_tokens or 0)
                            break

            return [
                TimeSeriesDataPoint(
                    date_label=b["label"],
                    requests=b["requests"],
                    cost=round(b["cost"], 6),
                    tokens=b["tokens"],
                )
                for b in buckets
            ]

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
            func.avg(GatewayRequestLog.llm_entailment_score).label("avg_entailment"),
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

        avg_entailment = (
            round(float(stats.avg_entailment), 2)
            if stats.avg_entailment is not None
            else None
        )
        return EvaluationSummaryResponse(
            avg_relevance_score=round(float(stats.avg_relevance), 2)
            if stats.avg_relevance is not None
            else None,
            avg_contradiction_score=round(float(stats.avg_contradiction), 2)
            if stats.avg_contradiction is not None
            else None,
            avg_entailment_score=avg_entailment,
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

    def get_latency_metrics(
        self,
        time_range: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        tenant_id: str | None = None,
    ) -> LatencyMetricsResponse:
        start_time, end_time, resolved_range = self._resolve_time_range(
            time_range=time_range, start_time=start_time, end_time=end_time
        )
        return self._calculate_latency_metrics(
            start_time=start_time,
            end_time=end_time,
            tenant_id=tenant_id,
            time_range_label=resolved_range,
        )

    def _calculate_latency_metrics(
        self,
        start_time: datetime,
        end_time: datetime,
        tenant_id: str | None,
        time_range_label: str | None = None,
    ) -> LatencyMetricsResponse:
        gw_query = self.db_session_read.query(
            GatewayRequestLog.created_at,
            GatewayRequestLog.latency_ms,
            GatewayRequestLog.provider_latency_ms,
            GatewayRequestLog.cache_lookup_latency_ms,
            GatewayRequestLog.latency_breakdown,
            GatewayRequestLog.routing_decision,
        ).filter(
            GatewayRequestLog.created_at >= start_time,
            GatewayRequestLog.created_at <= end_time,
        )
        if tenant_id:
            gw_query = gw_query.filter(GatewayRequestLog.tenant_id == tenant_id)

        rows = gw_query.all()
        total_requests = len(rows)

        all_latencies: list[float] = []
        provider_latencies: list[float] = []
        cache_lookup_latencies: list[float] = []

        component_sums: dict[str, float] = defaultdict(float)
        component_counts: dict[str, int] = defaultdict(int)

        routing_latencies: dict[str, list[float]] = defaultdict(list)

        for row in rows:
            breakdown_dict: dict[str, Any] = {}
            if isinstance(row.latency_breakdown, dict):
                breakdown_dict = row.latency_breakdown
            elif isinstance(row.latency_breakdown, str):
                try:
                    breakdown_dict = json.loads(row.latency_breakdown)
                except Exception:
                    breakdown_dict = {}

            # Total latency
            lat: float | None = None
            if row.latency_ms is not None:
                lat = float(row.latency_ms)
            elif breakdown_dict.get("total_ms") is not None:
                lat = float(breakdown_dict["total_ms"])

            if lat is not None:
                all_latencies.append(lat)
                decision = row.routing_decision or "UNKNOWN"
                routing_latencies[decision].append(lat)

            # Provider latency
            prov_lat: float | None = None
            if row.provider_latency_ms is not None:
                prov_lat = float(row.provider_latency_ms)
            elif breakdown_dict.get("provider_ms") is not None:
                prov_lat = float(breakdown_dict["provider_ms"])

            if prov_lat is not None:
                provider_latencies.append(prov_lat)

            # Cache lookup latency
            cache_lat: float | None = None
            if row.cache_lookup_latency_ms is not None:
                cache_lat = float(row.cache_lookup_latency_ms)
            elif breakdown_dict.get("cache_lookup_total_ms") is not None:
                cache_lat = float(breakdown_dict["cache_lookup_total_ms"])

            if cache_lat is not None:
                cache_lookup_latencies.append(cache_lat)

            # Component breakdown fields
            components = [
                "redis_exact_ms",
                "qdrant_exact_ms",
                "intent_classify_ms",
                "embedding_gen_ms",
                "qdrant_dense_ms",
                "qdrant_rrf_ms",
                "rerank_ms",
                "cache_lookup_total_ms",
                "provider_ms",
                "cache_write_ms",
                "total_ms",
            ]
            for comp in components:
                val = breakdown_dict.get(comp)
                if val is not None:
                    try:
                        v = float(val)
                        component_sums[comp] += v
                        component_counts[comp] += 1
                    except (ValueError, TypeError):
                        pass

        def avg_of(lst: list[float]) -> float | None:
            return round(sum(lst) / len(lst), 2) if lst else None

        avg_latency = avg_of(all_latencies) or 0.0
        avg_prov = avg_of(provider_latencies)
        avg_cache = avg_of(cache_lookup_latencies)

        # Percentiles
        percentiles = LatencyPercentiles(
            p50=_calculate_percentile(all_latencies, 50),
            p90=_calculate_percentile(all_latencies, 90),
            p95=_calculate_percentile(all_latencies, 95),
            p99=_calculate_percentile(all_latencies, 99),
        )

        # Component breakdown averages
        comp_breakdown = LatencyComponentBreakdown(
            redis_exact_ms=(
                round(
                    component_sums["redis_exact_ms"]
                    / component_counts["redis_exact_ms"],
                    2,
                )
                if component_counts["redis_exact_ms"] > 0
                else None
            ),
            qdrant_exact_ms=(
                round(
                    component_sums["qdrant_exact_ms"]
                    / component_counts["qdrant_exact_ms"],
                    2,
                )
                if component_counts["qdrant_exact_ms"] > 0
                else None
            ),
            intent_classify_ms=(
                round(
                    component_sums["intent_classify_ms"]
                    / component_counts["intent_classify_ms"],
                    2,
                )
                if component_counts["intent_classify_ms"] > 0
                else None
            ),
            embedding_gen_ms=(
                round(
                    component_sums["embedding_gen_ms"]
                    / component_counts["embedding_gen_ms"],
                    2,
                )
                if component_counts["embedding_gen_ms"] > 0
                else None
            ),
            qdrant_dense_ms=(
                round(
                    component_sums["qdrant_dense_ms"]
                    / component_counts["qdrant_dense_ms"],
                    2,
                )
                if component_counts["qdrant_dense_ms"] > 0
                else None
            ),
            qdrant_rrf_ms=(
                round(
                    component_sums["qdrant_rrf_ms"] / component_counts["qdrant_rrf_ms"],
                    2,
                )
                if component_counts["qdrant_rrf_ms"] > 0
                else None
            ),
            rerank_ms=(
                round(
                    component_sums["rerank_ms"] / component_counts["rerank_ms"],
                    2,
                )
                if component_counts["rerank_ms"] > 0
                else None
            ),
            cache_lookup_total_ms=(
                round(
                    component_sums["cache_lookup_total_ms"]
                    / component_counts["cache_lookup_total_ms"],
                    2,
                )
                if component_counts["cache_lookup_total_ms"] > 0
                else avg_cache
            ),
            provider_ms=(
                round(
                    component_sums["provider_ms"] / component_counts["provider_ms"],
                    2,
                )
                if component_counts["provider_ms"] > 0
                else avg_prov
            ),
            cache_write_ms=(
                round(
                    component_sums["cache_write_ms"]
                    / component_counts["cache_write_ms"],
                    2,
                )
                if component_counts["cache_write_ms"] > 0
                else None
            ),
            total_ms=(
                round(
                    component_sums["total_ms"] / component_counts["total_ms"],
                    2,
                )
                if component_counts["total_ms"] > 0
                else (avg_latency if all_latencies else None)
            ),
        )

        # By routing decision
        by_routing: list[LatencyByRoutingDecision] = []
        for decision, lat_list in sorted(routing_latencies.items()):
            by_routing.append(
                LatencyByRoutingDecision(
                    routing_decision=decision,
                    count=len(lat_list),
                    avg_latency_ms=avg_of(lat_list) or 0.0,
                    p50_latency_ms=_calculate_percentile(lat_list, 50),
                    p95_latency_ms=_calculate_percentile(lat_list, 95),
                )
            )

        # Time series
        time_series = self._calculate_latency_time_series(start_time, end_time, rows)

        return LatencyMetricsResponse(
            time_range=time_range_label,
            total_requests=total_requests,
            avg_latency_ms=avg_latency,
            avg_provider_latency_ms=avg_prov,
            avg_cache_lookup_latency_ms=avg_cache,
            percentiles=percentiles,
            component_breakdown=comp_breakdown,
            by_routing_decision=by_routing,
            time_series=time_series,
        )

    def _calculate_latency_time_series(
        self,
        start_time: datetime,
        end_time: datetime,
        rows: list[Any],
    ) -> list[LatencyTimeSeriesPoint]:
        duration = end_time - start_time
        if duration <= timedelta(hours=1):
            step = timedelta(minutes=5)
            fmt = "%H:%M"
        elif duration <= timedelta(hours=3):
            step = timedelta(minutes=15)
            fmt = "%H:%M"
        elif duration <= timedelta(hours=6):
            step = timedelta(minutes=30)
            fmt = "%H:%M"
        elif duration <= timedelta(hours=12):
            step = timedelta(hours=1)
            fmt = "%H:%M"
        elif duration <= timedelta(days=1):
            step = timedelta(hours=2)
            fmt = "%H:%M"
        elif duration <= timedelta(days=3):
            step = timedelta(hours=6)
            fmt = "%b %d %H:%M"
        elif duration <= timedelta(days=7):
            step = timedelta(days=1)
            fmt = "%Y-%m-%d"
        elif duration <= timedelta(days=14):
            step = timedelta(days=1)
            fmt = "%Y-%m-%d"
        else:
            step = timedelta(days=1)
            fmt = "%Y-%m-%d"

        buckets: list[dict[str, Any]] = []
        curr = start_time
        while curr < end_time:
            next_curr = min(curr + step, end_time)
            buckets.append(
                {
                    "start": curr,
                    "end": next_curr,
                    "time_label": curr.strftime(fmt),
                    "total_lats": [],
                    "provider_lats": [],
                    "cache_lats": [],
                }
            )
            curr = next_curr

        for row in rows:
            if not row.created_at:
                continue
            cat = row.created_at
            if cat.tzinfo is None:
                cat = cat.replace(tzinfo=timezone.utc)

            for b in buckets:
                if b["start"] <= cat < b["end"]:
                    lat = row.latency_ms
                    if lat is None and isinstance(row.latency_breakdown, dict):
                        lat = row.latency_breakdown.get("total_ms")
                    if lat is not None:
                        try:
                            b["total_lats"].append(float(lat))
                        except (ValueError, TypeError):
                            pass

                    prov = row.provider_latency_ms
                    if prov is None and isinstance(row.latency_breakdown, dict):
                        prov = row.latency_breakdown.get("provider_ms")
                    if prov is not None:
                        try:
                            b["provider_lats"].append(float(prov))
                        except (ValueError, TypeError):
                            pass

                    cache_l = row.cache_lookup_latency_ms
                    if cache_l is None and isinstance(row.latency_breakdown, dict):
                        cache_l = row.latency_breakdown.get("cache_lookup_total_ms")
                    if cache_l is not None:
                        try:
                            b["cache_lats"].append(float(cache_l))
                        except (ValueError, TypeError):
                            pass
                    break

        result: list[LatencyTimeSeriesPoint] = []
        for b in buckets:
            t_lats = b["total_lats"]
            p_lats = b["provider_lats"]
            c_lats = b["cache_lats"]
            result.append(
                LatencyTimeSeriesPoint(
                    time_label=b["time_label"],
                    timestamp=b["start"],
                    avg_total_latency_ms=round(sum(t_lats) / len(t_lats), 2)
                    if t_lats
                    else 0.0,
                    avg_provider_latency_ms=round(sum(p_lats) / len(p_lats), 2)
                    if p_lats
                    else None,
                    avg_cache_lookup_latency_ms=round(sum(c_lats) / len(c_lats), 2)
                    if c_lats
                    else None,
                    requests=len(t_lats),
                )
            )

        return result
