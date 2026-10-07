import logging
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.database import SessionLocal, SessionLocalRead
from app.modules.llm_usage.llm_usage_model import LlmUsageLog
from app.modules.llm_usage.llm_usage_schema import LlmUsageLogCreate, LlmUsageLogUpdate

logger = logging.getLogger(__name__)


class LlmUsageService:
    def __init__(
        self,
        session_factory: Callable[[], Session] | Session | None = None,
        session_factory_read: Callable[[], Session] | Session | None = None,
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
            session_factory_read = SessionLocalRead

        self._session_factory = session_factory
        self._session_factory_read = session_factory_read
        self._db_session = db_session
        self._db_session_read = db_session_read

    @contextmanager
    def _get_session(self):
        if self._db_session is not None:
            yield self._db_session
        else:
            with self._session_factory() as s:
                yield s

    @contextmanager
    def _get_read_session(self):
        if self._db_session_read is not None:
            yield self._db_session_read
        elif self._db_session is not None:
            yield self._db_session
        elif self._session_factory_read is not None:
            with self._session_factory_read() as s:
                yield s
        else:
            with self._session_factory() as s:
                yield s

    def get_llm_usage_logs(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[LlmUsageLog]:
        try:
            with self._get_read_session() as s:
                query = s.query(LlmUsageLog)

                if start_time:
                    query = query.filter(LlmUsageLog.created_at >= start_time)
                if end_time:
                    query = query.filter(LlmUsageLog.created_at <= end_time)

                return (
                    query.order_by(LlmUsageLog.created_at.desc())
                    .offset(skip)
                    .limit(limit)
                    .all()
                )
        except Exception:
            logger.exception("Failed to get LLM usage logs")
            raise

    def get_llm_usage_log(self, log_id: int) -> LlmUsageLog | None:
        with self._get_read_session() as s:
            return s.query(LlmUsageLog).filter(LlmUsageLog.id == log_id).first()

    def create_llm_usage_log(self, log_data: LlmUsageLogCreate) -> LlmUsageLog:
        llm_usage_log = LlmUsageLog(**log_data.model_dump())
        with self._get_session() as s:
            try:
                s.add(llm_usage_log)
                s.commit()
                s.refresh(llm_usage_log)
                return llm_usage_log
            except Exception:
                logger.exception("Failed to create LLM usage log")
                s.rollback()
                raise

    def update_llm_usage_log(
        self, log_id: int, log_data: LlmUsageLogUpdate
    ) -> LlmUsageLog | None:
        with self._get_session() as s:
            llm_usage_log = (
                s.query(LlmUsageLog).filter(LlmUsageLog.id == log_id).first()
            )
            if not llm_usage_log:
                return None

            update_data = log_data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(llm_usage_log, key, value)

            try:
                s.commit()
                s.refresh(llm_usage_log)
                return llm_usage_log
            except Exception:
                logger.exception(f"Failed to update LLM usage log {log_id}")
                s.rollback()
                raise

    def delete_llm_usage_log(self, log_id: int) -> bool:
        with self._get_session() as s:
            llm_usage_log = (
                s.query(LlmUsageLog).filter(LlmUsageLog.id == log_id).first()
            )
            if not llm_usage_log:
                return False

            try:
                s.delete(llm_usage_log)
                s.commit()
                return True
            except Exception:
                logger.exception(f"Failed to delete LLM usage log {log_id}")
                s.rollback()
                raise

    def get_pending_cost_calculations(self, limit: int = 50) -> list[LlmUsageLog]:
        """Fetch pending requests for background cost calculation."""
        try:
            with self._get_read_session() as s:
                return (
                    s.query(LlmUsageLog)
                    .filter(LlmUsageLog.cost_calculated.is_(False))
                    .order_by(LlmUsageLog.id.asc())
                    .limit(limit)
                    .all()
                )
        except Exception:
            logger.exception("Failed to get pending cost calculations")
            raise

    def bulk_update_costs(self, updates: list[dict]) -> bool:
        """
        Bulk update cost and cost_calculated fields for LLM usage logs.
        `updates` should be a list of dicts, e.g.:
        [{"id": 1, "cost": Decimal("0.05"), "cost_calculated": True}, ...]
        """
        with self._get_session() as s:
            try:
                s.bulk_update_mappings(LlmUsageLog, updates)
                s.commit()
                return True
            except Exception:
                logger.exception("Failed to bulk update LLM usage logs")
                s.rollback()
                raise
