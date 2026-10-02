import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.llm_usage.llm_usage_model import LlmUsageLog
from app.modules.llm_usage.llm_usage_schema import LlmUsageLogCreate, LlmUsageLogUpdate

logger = logging.getLogger(__name__)


class LlmUsageService:
    def __init__(self, db_session: Session, db_session_read: Session):
        self.db_session = db_session
        self.db_session_read = db_session_read

    def get_llm_usage_logs(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[LlmUsageLog]:
        try:
            query = self.db_session.query(LlmUsageLog)

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
        except Exception as e:
            logger.exception("Failed to get LLM usage logs")
            raise e

    def get_llm_usage_log(self, log_id: int) -> LlmUsageLog | None:
        return (
            self.db_session.query(LlmUsageLog).filter(LlmUsageLog.id == log_id).first()
        )

    def create_llm_usage_log(self, log_data: LlmUsageLogCreate) -> LlmUsageLog:
        llm_usage_log = LlmUsageLog(**log_data.model_dump())
        try:
            self.db_session.add(llm_usage_log)
            self.db_session.commit()
            self.db_session.refresh(llm_usage_log)
            return llm_usage_log
        except Exception as e:
            logger.exception("Failed to create LLM usage log")
            self.db_session.rollback()
            raise e

    def update_llm_usage_log(
        self, log_id: int, log_data: LlmUsageLogUpdate
    ) -> LlmUsageLog | None:
        llm_usage_log = self.get_llm_usage_log(log_id)
        if not llm_usage_log:
            return None

        update_data = log_data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(llm_usage_log, key, value)

        try:
            self.db_session.commit()
            self.db_session.refresh(llm_usage_log)
            return llm_usage_log
        except Exception as e:
            logger.exception(f"Failed to update LLM usage log {log_id}")
            self.db_session.rollback()
            raise e

    def delete_llm_usage_log(self, log_id: int) -> bool:
        llm_usage_log = self.get_llm_usage_log(log_id)
        if not llm_usage_log:
            return False

        try:
            self.db_session.delete(llm_usage_log)
            self.db_session.commit()
            return True
        except Exception as e:
            logger.exception(f"Failed to delete LLM usage log {log_id}")
            self.db_session.rollback()
            raise e
