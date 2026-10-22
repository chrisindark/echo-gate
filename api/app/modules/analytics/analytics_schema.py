from datetime import datetime

from pydantic import BaseModel


class KpiSummaryResponse(BaseModel):
    total_requests: int
    total_cost: float
    total_tokens: int
    avg_latency_ms: float
    cache_hit_rate: float


class TimeSeriesDataPoint(BaseModel):
    date_label: str  # e.g., "2023-10-01"
    requests: int
    cost: float
    tokens: int


class CachePerformanceResponse(BaseModel):
    llm_calls: int
    redis_exact: int
    qdrant_exact: int
    qdrant_semantic: int
    total: int
    cache_hit_percentage: float


class ModelBreakdownItem(BaseModel):
    model: str
    provider: str
    requests: int
    total_cost: float
    total_tokens: int


class EvaluationSummaryResponse(BaseModel):
    avg_relevance_score: float | None
    avg_contradiction_score: float | None
    avg_entailment_score: float | None = None
    false_positive_rate: float | None
    evaluated_count: int


class TopIntentItem(BaseModel):
    intent: str
    count: int


class LatencyComponentBreakdown(BaseModel):
    redis_exact_ms: float | None = None
    qdrant_exact_ms: float | None = None
    intent_classify_ms: float | None = None
    embedding_gen_ms: float | None = None
    qdrant_dense_ms: float | None = None
    qdrant_rrf_ms: float | None = None
    rerank_ms: float | None = None
    cache_lookup_total_ms: float | None = None
    provider_ms: float | None = None
    cache_write_ms: float | None = None
    total_ms: float | None = None


class LatencyPercentiles(BaseModel):
    p50: float | None = None
    p90: float | None = None
    p95: float | None = None
    p99: float | None = None


class LatencyByRoutingDecision(BaseModel):
    routing_decision: str
    count: int
    avg_latency_ms: float
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None


class LatencyTimeSeriesPoint(BaseModel):
    time_label: str
    timestamp: datetime
    avg_total_latency_ms: float
    avg_provider_latency_ms: float | None = None
    avg_cache_lookup_latency_ms: float | None = None
    requests: int


class LatencyMetricsResponse(BaseModel):
    time_range: str | None = None
    total_requests: int
    avg_latency_ms: float
    avg_provider_latency_ms: float | None = None
    avg_cache_lookup_latency_ms: float | None = None
    percentiles: LatencyPercentiles
    component_breakdown: LatencyComponentBreakdown
    by_routing_decision: list[LatencyByRoutingDecision]
    time_series: list[LatencyTimeSeriesPoint]


class AnalyticsDashboardResponse(BaseModel):
    kpis: KpiSummaryResponse
    time_series: list[TimeSeriesDataPoint]
    cache_performance: CachePerformanceResponse
    model_breakdown: list[ModelBreakdownItem]
    evaluations: EvaluationSummaryResponse
    top_intents: list[TopIntentItem]
    latency_metrics: LatencyMetricsResponse | None = None
