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
    avg_instruction_score: float | None
    false_positive_rate: float | None
    evaluated_count: int


class TopIntentItem(BaseModel):
    intent: str
    count: int


class AnalyticsDashboardResponse(BaseModel):
    kpis: KpiSummaryResponse
    time_series: list[TimeSeriesDataPoint]
    cache_performance: CachePerformanceResponse
    model_breakdown: list[ModelBreakdownItem]
    evaluations: EvaluationSummaryResponse
    top_intents: list[TopIntentItem]
