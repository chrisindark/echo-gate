import logging
from collections.abc import Callable
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.core.database import SessionLocal, SessionLocalRead
from app.modules.gateway_requests.gateway_requests_model import GatewayRequestLog
from app.modules.gateway_requests.gateway_requests_schema import (
    GatewayRequestLogCreate,
    GatewayRequestLogUpdate,
)

logger = logging.getLogger(__name__)


class GatewayRequestsService:
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

    def log_request(self, log_data: GatewayRequestLogCreate) -> GatewayRequestLog:
        """Create a new gateway request log."""
        request_log = GatewayRequestLog(**log_data.model_dump())
        with self._get_session() as s:
            try:
                s.add(request_log)
                s.commit()
                s.refresh(request_log)
                return request_log
            except Exception:
                logger.exception("Failed to create gateway request log")
                s.rollback()
                raise

    def get_request_log(self, log_id: int) -> GatewayRequestLog | None:
        """Get a specific request log by ID."""
        with self._get_session() as s:
            return (
                s.query(GatewayRequestLog)
                .filter(GatewayRequestLog.id == log_id)
                .first()
            )

    def get_request_log_by_hash(self, exact_hash: str) -> GatewayRequestLog | None:
        """Get a specific request log by exact hash."""
        with self._get_read_session() as s:
            return (
                s.query(GatewayRequestLog)
                .filter(GatewayRequestLog.exact_hash == exact_hash)
                .order_by(GatewayRequestLog.created_at.desc())
                .first()
            )

    def get_request_logs(
        self,
        skip: int = 0,
        limit: int = 100,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> list[GatewayRequestLog]:
        """Fetch a paginated list of request logs."""
        try:
            with self._get_read_session() as s:
                query = s.query(GatewayRequestLog)

                if tenant_id:
                    query = query.filter(GatewayRequestLog.tenant_id == tenant_id)
                if user_id:
                    query = query.filter(GatewayRequestLog.user_id == user_id)

                return (
                    query.order_by(GatewayRequestLog.created_at.desc())
                    .offset(skip)
                    .limit(limit)
                    .all()
                )
        except Exception:
            logger.exception("Failed to get gateway request logs")
            raise

    def get_pending_evaluations(self, limit: int = 50) -> list[GatewayRequestLog]:
        """Fetch pending requests for background evaluation."""
        try:
            with self._get_read_session() as s:
                return (
                    s.query(GatewayRequestLog)
                    .filter(GatewayRequestLog.evaluation_status == "PENDING")
                    .filter(
                        GatewayRequestLog.routing_decision.in_(
                            ["LLM", "QDRANT_SEMANTIC"]
                        )
                    )
                    .order_by(GatewayRequestLog.id.asc())
                    .limit(limit)
                    .all()
                )
        except Exception:
            logger.exception("Failed to get pending evaluations")
            raise

    def update_evaluation(
        self, log_id: int, update_data: GatewayRequestLogUpdate
    ) -> GatewayRequestLog | None:
        """Update a log after evaluation is complete."""
        with self._get_session() as s:
            request_log = (
                s.query(GatewayRequestLog)
                .filter(GatewayRequestLog.id == log_id)
                .first()
            )
            if not request_log:
                return None

            update_dict = update_data.model_dump(exclude_unset=True)
            for key, value in update_dict.items():
                setattr(request_log, key, value)

            try:
                s.commit()
                s.refresh(request_log)
                return request_log
            except Exception:
                logger.exception(f"Failed to update gateway request log {log_id}")
                s.rollback()
                raise

    def delete_request_log(self, log_id: int) -> bool:
        """Delete an existing gateway request log."""
        with self._get_session() as s:
            request_log = (
                s.query(GatewayRequestLog)
                .filter(GatewayRequestLog.id == log_id)
                .first()
            )
            if not request_log:
                return False

            try:
                s.delete(request_log)
                s.commit()
                return True
            except Exception:
                logger.exception(f"Failed to delete gateway request log {log_id}")
                s.rollback()
                raise
