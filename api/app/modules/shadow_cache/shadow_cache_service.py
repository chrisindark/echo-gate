import logging
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.config import config
from app.core.database import SessionFactory, SessionLocal
from app.modules.shadow_cache.shadow_cache_model import ShadowCacheEvaluation
from app.modules.shadow_cache.shadow_cache_schema import (
    ReadinessStatus,
    ShadowCacheEvaluationCreate,
    ShadowCacheSummaryResponse,
    ShadowIntentBreakdown,
    ShadowMatchType,
    ShadowMatchTypeBreakdown,
    ShadowModelBreakdown,
    ShadowReadiness,
)

logger = logging.getLogger(__name__)


class ShadowCacheService:
    """Persistence and aggregation for shadow cache evaluations."""

    _session_factory: SessionFactory | None
    _session_factory_read: SessionFactory | None
    _db_session: Session | None
    _db_session_read: Session | None

    def __init__(
        self,
        session_factory: SessionFactory | Session | None = None,
        session_factory_read: SessionFactory | Session | None = None,
        db_session: Session | None = None,
        db_session_read: Session | None = None,
    ):
        if isinstance(session_factory, Session):
            db_session = session_factory
            session_factory = None
        if isinstance(session_factory_read, Session):
            db_session_read = session_factory_read
            session_factory_read = None

        if session_factory is None and db_session is None:
            session_factory = SessionLocal
        if session_factory_read is None and db_session_read is None:
            session_factory_read = SessionLocal

        self._session_factory = session_factory
        self._session_factory_read = session_factory_read
        self._db_session = db_session
        self._db_session_read = db_session_read

    @contextmanager
    def _get_session(self) -> Generator[Session]:
        if self._db_session is not None:
            yield self._db_session
        elif self._session_factory is not None:
            with self._session_factory() as s:
                yield s
        else:
            with SessionLocal() as s:
                yield s

    @contextmanager
    def _get_read_session(self) -> Generator[Session]:
        if self._db_session_read is not None:
            yield self._db_session_read
        elif self._db_session is not None:
            yield self._db_session
        elif self._session_factory_read is not None:
            with self._session_factory_read() as s:
                yield s
        elif self._session_factory is not None:
            with self._session_factory() as s:
                yield s
        else:
            with SessionLocal() as s:
                yield s

    def log_evaluation(
        self, evaluation: ShadowCacheEvaluationCreate
    ) -> ShadowCacheEvaluation:
        """Persist one shadow evaluation."""
        # mode="json" normalises the match-type enum to its string value, which
        # is what the varchar column expects.
        row = ShadowCacheEvaluation(**evaluation.model_dump(mode="json"))
        with self._get_session() as s:
            try:
                s.add(row)
                s.commit()
                s.refresh(row)
                return row
            except Exception:
                logger.exception("Failed to persist shadow cache evaluation")
                s.rollback()
                raise

    def get_evaluations(
        self,
        skip: int = 0,
        limit: int = 100,
        tenant_id: str | None = None,
        would_have_hit: bool | None = None,
        match_type: str | None = None,
        is_equivalent: bool | None = None,
        model: str | None = None,
        intent: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[list[ShadowCacheEvaluation], int]:
        """Fetch a filtered, paginated page of shadow evaluations."""
        with self._get_read_session() as s:
            query = s.query(ShadowCacheEvaluation)
            if tenant_id:
                query = query.filter(ShadowCacheEvaluation.tenant_id == tenant_id)
            if would_have_hit is not None:
                query = query.filter(
                    ShadowCacheEvaluation.would_have_hit == would_have_hit
                )
            if match_type:
                query = query.filter(ShadowCacheEvaluation.match_type == match_type)
            if is_equivalent is not None:
                query = query.filter(
                    ShadowCacheEvaluation.is_equivalent == is_equivalent
                )
            if model:
                query = query.filter(ShadowCacheEvaluation.model == model)
            if intent:
                query = query.filter(ShadowCacheEvaluation.intent == intent)
            if start_time:
                query = query.filter(ShadowCacheEvaluation.created_at >= start_time)
            if end_time:
                query = query.filter(ShadowCacheEvaluation.created_at <= end_time)

            total = query.count()
            items = (
                query.order_by(ShadowCacheEvaluation.created_at.desc())
                .offset(skip)
                .limit(limit)
                .all()
            )
            return items, total

    def get_summary(
        self,
        start_time: datetime,
        end_time: datetime,
        tenant_id: str | None = None,
        time_range: str | None = None,
    ) -> ShadowCacheSummaryResponse:
        """Aggregate the shadow window into operator-facing readiness metrics."""
        with self._get_read_session() as s:
            base = s.query(ShadowCacheEvaluation).filter(
                ShadowCacheEvaluation.created_at >= start_time,
                ShadowCacheEvaluation.created_at <= end_time,
            )
            if tenant_id:
                base = base.filter(ShadowCacheEvaluation.tenant_id == tenant_id)

            totals = base.with_entities(
                func.count(ShadowCacheEvaluation.id).label("evaluations"),
                func.sum(
                    case((ShadowCacheEvaluation.would_have_hit.is_(True), 1), else_=0)
                ).label("would_have_hits"),
                func.sum(
                    case((ShadowCacheEvaluation.is_equivalent.is_(True), 1), else_=0)
                ).label("equivalent"),
                func.sum(
                    case((ShadowCacheEvaluation.is_equivalent.is_(False), 1), else_=0)
                ).label("non_equivalent"),
                func.sum(
                    case((ShadowCacheEvaluation.is_equivalent.isnot(None), 1), else_=0)
                ).label("compared"),
                func.avg(ShadowCacheEvaluation.projected_latency_saved_ms).label(
                    "avg_saved"
                ),
                func.sum(ShadowCacheEvaluation.projected_latency_saved_ms).label(
                    "total_saved"
                ),
                func.avg(ShadowCacheEvaluation.cache_lookup_latency_ms).label(
                    "avg_lookup"
                ),
                func.avg(ShadowCacheEvaluation.provider_latency_ms).label(
                    "avg_provider"
                ),
            ).one()

            total_evaluations = int(totals.evaluations or 0)
            would_have_hits = int(totals.would_have_hits or 0)
            compared = int(totals.compared or 0)
            equivalent = int(totals.equivalent or 0)
            non_equivalent = int(totals.non_equivalent or 0)

            hit_rate = would_have_hits / total_evaluations if total_evaluations else 0.0
            equivalence_rate = equivalent / compared if compared else None

            match_rows = (
                base.with_entities(
                    ShadowCacheEvaluation.match_type.label("match_type"),
                    func.count(ShadowCacheEvaluation.id).label("count"),
                )
                .group_by(ShadowCacheEvaluation.match_type)
                .order_by(func.count(ShadowCacheEvaluation.id).desc())
                .all()
            )

            intent_rows = (
                base.with_entities(
                    ShadowCacheEvaluation.intent.label("intent"),
                    func.count(ShadowCacheEvaluation.id).label("evaluations"),
                    func.sum(
                        case(
                            (ShadowCacheEvaluation.would_have_hit.is_(True), 1),
                            else_=0,
                        )
                    ).label("would_have_hits"),
                )
                .filter(ShadowCacheEvaluation.intent.isnot(None))
                .group_by(ShadowCacheEvaluation.intent)
                .order_by(func.count(ShadowCacheEvaluation.id).desc())
                .limit(10)
                .all()
            )

            model_rows = (
                base.with_entities(
                    ShadowCacheEvaluation.model.label("model"),
                    func.max(ShadowCacheEvaluation.provider).label("provider"),
                    func.count(ShadowCacheEvaluation.id).label("evaluations"),
                    func.sum(
                        case(
                            (ShadowCacheEvaluation.would_have_hit.is_(True), 1),
                            else_=0,
                        )
                    ).label("would_have_hits"),
                )
                .filter(ShadowCacheEvaluation.model.isnot(None))
                .group_by(ShadowCacheEvaluation.model)
                .order_by(func.count(ShadowCacheEvaluation.id).desc())
                .limit(10)
                .all()
            )

        readiness = self._evaluate_readiness(
            total_evaluations=total_evaluations,
            hit_rate=hit_rate,
            compared=compared,
            equivalence_rate=equivalence_rate,
        )

        return ShadowCacheSummaryResponse(
            cache_mode=config.CACHE_MODE,
            start_time=start_time,
            end_time=end_time,
            time_range=time_range,
            total_evaluations=total_evaluations,
            would_have_hits=would_have_hits,
            hit_rate=round(hit_rate, 4),
            match_type_breakdown=[
                ShadowMatchTypeBreakdown(
                    match_type=self._coerce_match_type(row.match_type),
                    count=int(row.count),
                )
                for row in match_rows
            ],
            compared_evaluations=compared,
            equivalent_evaluations=equivalent,
            non_equivalent_evaluations=non_equivalent,
            equivalence_rate=(
                round(equivalence_rate, 4) if equivalence_rate is not None else None
            ),
            avg_latency_saved_ms=(
                round(float(totals.avg_saved), 2)
                if totals.avg_saved is not None
                else None
            ),
            projected_latency_saved_ms=(
                int(totals.total_saved) if totals.total_saved is not None else None
            ),
            avg_cache_lookup_ms=(
                round(float(totals.avg_lookup), 2)
                if totals.avg_lookup is not None
                else None
            ),
            avg_provider_ms=(
                round(float(totals.avg_provider), 2)
                if totals.avg_provider is not None
                else None
            ),
            by_intent=[
                ShadowIntentBreakdown(
                    intent=row.intent,
                    evaluations=int(row.evaluations),
                    would_have_hits=int(row.would_have_hits or 0),
                    hit_rate=round(
                        (int(row.would_have_hits or 0) / int(row.evaluations)), 4
                    )
                    if row.evaluations
                    else 0.0,
                )
                for row in intent_rows
            ],
            by_model=[
                ShadowModelBreakdown(
                    model=row.model,
                    provider=row.provider,
                    evaluations=int(row.evaluations),
                    would_have_hits=int(row.would_have_hits or 0),
                    hit_rate=round(
                        (int(row.would_have_hits or 0) / int(row.evaluations)), 4
                    )
                    if row.evaluations
                    else 0.0,
                )
                for row in model_rows
            ],
            readiness=readiness,
        )

    @staticmethod
    def _coerce_match_type(value: str) -> ShadowMatchType:
        try:
            return ShadowMatchType(value)
        except ValueError:
            return ShadowMatchType.MISS

    @staticmethod
    def _evaluate_readiness(
        total_evaluations: int,
        hit_rate: float,
        compared: int,
        equivalence_rate: float | None,
    ) -> ShadowReadiness:
        """Summarise whether the numbers justify flipping to ``active`` mode.

        The gate is deliberately conservative: enough observations, a meaningful
        would-be hit rate, and a judged-equivalence rate at or above the
        configured floor. Anything short of that is surfaced with reasons rather
        than a bare boolean.
        """
        min_evaluations = config.SHADOW_READINESS_MIN_EVALUATIONS
        min_hit_rate = config.SHADOW_READINESS_MIN_HIT_RATE
        min_equivalence_rate = config.SHADOW_READINESS_MIN_EQUIVALENCE_RATE

        reasons: list[str] = []
        if total_evaluations == 0:
            return ShadowReadiness(
                status=ReadinessStatus.NOT_READY,
                reasons=["No shadow evaluations recorded in this window."],
                min_evaluations=min_evaluations,
                min_hit_rate=min_hit_rate,
                min_equivalence_rate=min_equivalence_rate,
            )

        if total_evaluations < min_evaluations:
            reasons.append(
                f"{total_evaluations} of {min_evaluations} required evaluations collected."
            )
        if hit_rate < min_hit_rate:
            reasons.append(
                f"Would-be hit rate {hit_rate:.1%} is below the {min_hit_rate:.1%} floor."
            )
        if compared == 0:
            reasons.append(
                "No shadow match has been compared against a fresh answer yet."
            )
        elif equivalence_rate is not None and equivalence_rate < min_equivalence_rate:
            reasons.append(
                f"Judge equivalence {equivalence_rate:.1%} is below the "
                f"{min_equivalence_rate:.1%} floor."
            )

        if total_evaluations < min_evaluations or compared == 0:
            status = ReadinessStatus.WARMING_UP
        elif reasons:
            status = ReadinessStatus.NOT_READY
        else:
            status = ReadinessStatus.READY

        return ShadowReadiness(
            status=status,
            reasons=reasons,
            min_evaluations=min_evaluations,
            min_hit_rate=min_hit_rate,
            min_equivalence_rate=min_equivalence_rate,
        )
