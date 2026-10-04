from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class RoutingDecision(str, Enum):
    LLM = "LLM"
    REDIS_EXACT = "REDIS_EXACT"
    QDRANT_EXACT = "QDRANT_EXACT"
    QDRANT_SEMANTIC = "QDRANT_SEMANTIC"
    QDRANT_FAST = "QDRANT_FAST"


class EvaluationStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    EVALUATED = "EVALUATED"
    SKIPPED = "SKIPPED"


class GatewayRequestLogBase(BaseModel):
    query_text: str
    response_text: str | None = None
    routing_decision: RoutingDecision
    provider: str | None = None
    model: str | None = None
    point_id: str | None = None
    exact_hash: str | None = None
    intent: str | None = None
    core_operation: str | None = None
    core_subject: str | None = None
    subject_modifier: str | None = None
    action_modifier: str | None = None
    rerank_score: float | None = None
    latency_ms: int | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    session_id: str | None = None
    conversation_id: str | None = None


class GatewayRequestLogCreate(GatewayRequestLogBase):
    evaluation_status: EvaluationStatus = EvaluationStatus.PENDING


class GatewayRequestLogUpdate(BaseModel):
    evaluation_status: EvaluationStatus | None = None
    llm_relevance_score: float | None = None
    llm_contradiction_score: float | None = None
    llm_instruction_score: float | None = None
    is_false_positive: bool | None = None
    error_message: str | None = None


class GatewayRequestLogResponse(GatewayRequestLogBase):
    id: int
    created_at: datetime
    updated_at: datetime
    evaluation_status: str
    llm_relevance_score: float | None = None
    llm_contradiction_score: float | None = None
    llm_instruction_score: float | None = None
    is_false_positive: bool | None = None
    error_message: str | None = None

    model_config = ConfigDict(from_attributes=True)
