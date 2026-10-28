import logging
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.database import SessionFactory, SessionLocal
from app.modules.cache_audit.cache_audit_model import CacheEvaluation
from app.modules.cache_audit.cache_audit_schema import (
    CacheAuditCreate,
    CacheAuditSummary,
)

logger = logging.getLogger(__name__)


class CacheAuditService:
    """Persistence and aggregation for sampled cache audit evaluations."""

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
        elif self._session_factory_read is not None:
            with self._session_factory_read() as s:
                yield s
        else:
            with SessionLocal() as s:
                yield s

    def log_audit(self, audit: CacheAuditCreate) -> CacheEvaluation:
        """Persist one cache audit evaluation."""
        row = CacheEvaluation(**audit.model_dump())
        with self._get_session() as s:
            try:
                s.add(row)
                s.commit()
                s.refresh(row)
                return row
            except Exception:
                logger.exception("Failed to persist cache audit evaluation")
                s.rollback()
                raise

    def get_audits(
        self,
        skip: int = 0,
        limit: int = 100,
        tenant_id: str | None = None,
        is_equivalent: bool | None = None,
        evicted: bool | None = None,
        model: str | None = None,
        intent: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[list[CacheEvaluation], int]:
        """Fetch a filtered, paginated page of cache audits."""
        with self._get_read_session() as s:
            query = s.query(CacheEvaluation)
            if tenant_id:
                query = query.filter(CacheEvaluation.tenant_id == tenant_id)
            if is_equivalent is not None:
                query = query.filter(CacheEvaluation.is_equivalent == is_equivalent)
            if evicted is not None:
                query = query.filter(CacheEvaluation.evicted == evicted)
            if model:
                query = query.filter(CacheEvaluation.model == model)
            if intent:
                query = query.filter(CacheEvaluation.intent == intent)
            if start_time:
                query = query.filter(CacheEvaluation.created_at >= start_time)
            if end_time:
                query = query.filter(CacheEvaluation.created_at <= end_time)

            total = query.count()
            rows = (
                query.order_by(CacheEvaluation.created_at.desc())
                .offset(skip)
                .limit(limit)
                .all()
            )
            return rows, total

    def get_audit_summary(
        self,
        tenant_id: str | None = None,
        model: str | None = None,
        intent: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> CacheAuditSummary:
        """Aggregate audit equivalence stats across sampled cache hits."""
        with self._get_read_session() as s:
            query = s.query(CacheEvaluation)
            if tenant_id:
                query = query.filter(CacheEvaluation.tenant_id == tenant_id)
            if model:
                query = query.filter(CacheEvaluation.model == model)
            if intent:
                query = query.filter(CacheEvaluation.intent == intent)
            if start_time:
                query = query.filter(CacheEvaluation.created_at >= start_time)
            if end_time:
                query = query.filter(CacheEvaluation.created_at <= end_time)

            total = query.count()
            if total == 0:
                return CacheAuditSummary()

            equivalent = query.filter(CacheEvaluation.is_equivalent.is_(True)).count()
            non_equivalent = query.filter(
                CacheEvaluation.is_equivalent.is_(False)
            ).count()
            uncompared = total - (equivalent + non_equivalent)
            evicted = query.filter(CacheEvaluation.evicted.is_(True)).count()

            compared = equivalent + non_equivalent
            rate = round(equivalent / compared, 4) if compared > 0 else 0.0

            return CacheAuditSummary(
                total_audits=total,
                equivalent_count=equivalent,
                non_equivalent_count=non_equivalent,
                uncompared_count=uncompared,
                equivalence_rate=rate,
                evicted_count=evicted,
            )
