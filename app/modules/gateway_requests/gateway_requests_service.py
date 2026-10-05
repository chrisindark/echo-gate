import logging

from sqlalchemy.orm import Session

from app.modules.gateway_requests.gateway_requests_model import GatewayRequestLog
from app.modules.gateway_requests.gateway_requests_schema import (
    GatewayRequestLogCreate,
    GatewayRequestLogUpdate,
)

logger = logging.getLogger(__name__)


class GatewayRequestsService:
    def __init__(self, db_session: Session, db_session_read: Session):
        self.db_session = db_session
        self.db_session_read = db_session_read

    def log_request(self, log_data: GatewayRequestLogCreate) -> GatewayRequestLog:
        """Create a new gateway request log."""
        request_log = GatewayRequestLog(**log_data.model_dump())
        try:
            self.db_session.add(request_log)
            self.db_session.commit()
            self.db_session.refresh(request_log)
            return request_log
        except Exception as e:
            logger.exception("Failed to create gateway request log")
            self.db_session.rollback()
            raise e

    def get_request_log(self, log_id: int) -> GatewayRequestLog | None:
        """Get a specific request log by ID (using write session for subsequent updates)."""
        return (
            self.db_session.query(GatewayRequestLog)
            .filter(GatewayRequestLog.id == log_id)
            .first()
        )

    def get_request_log_by_hash(self, exact_hash: str) -> GatewayRequestLog | None:
        """Get a specific request log by exact hash."""
        return (
            self.db_session_read.query(GatewayRequestLog)
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
            query = self.db_session_read.query(GatewayRequestLog)

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
        except Exception as e:
            logger.exception("Failed to get gateway request logs")
            raise e

    def get_pending_evaluations(self, limit: int = 50) -> list[GatewayRequestLog]:
        """Fetch pending requests for background evaluation."""
        try:
            return (
                self.db_session_read.query(GatewayRequestLog)
                .filter(GatewayRequestLog.evaluation_status == "PENDING")
                .filter(
                    GatewayRequestLog.routing_decision.in_(["LLM", "QDRANT_SEMANTIC"])
                )
                .order_by(GatewayRequestLog.id.asc())
                .limit(limit)
                .all()
            )
        except Exception as e:
            logger.exception("Failed to get pending evaluations")
            raise e

    def update_evaluation(
        self, log_id: int, update_data: GatewayRequestLogUpdate
    ) -> GatewayRequestLog | None:
        """Update a log after evaluation is complete."""
        request_log = self.get_request_log(log_id)
        if not request_log:
            return None

        update_dict = update_data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(request_log, key, value)

        try:
            self.db_session.commit()
            self.db_session.refresh(request_log)
            return request_log
        except Exception as e:
            logger.exception(f"Failed to update gateway request log {log_id}")
            self.db_session.rollback()
            raise e

    def delete_request_log(self, log_id: int) -> bool:
        """Delete an existing gateway request log."""
        request_log = self.get_request_log(log_id)
        if not request_log:
            return False

        try:
            self.db_session.delete(request_log)
            self.db_session.commit()
            return True
        except Exception as e:
            logger.exception(f"Failed to delete gateway request log {log_id}")
            self.db_session.rollback()
            raise e
