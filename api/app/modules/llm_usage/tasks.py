import logging
import time
import uuid
from decimal import Decimal

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.core.database import task_db_session
from app.core.helpers import dispatch_task
from app.core.pricing import get_model_prices
from app.modules.llm_usage.llm_usage_schema import LlmUsageLogUpdate
from app.modules.llm_usage.llm_usage_service import LlmUsageService
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


def execute_process_uncalculated_costs_job(
    task_id: str | None = None, db_session: Session | None = None
) -> str:
    """
    Executes cost calculations for pending usage logs.
    Shared across Celery and in-process background task fallback.
    """
    tid = task_id or f"bg-{uuid.uuid4()}"
    logger.info(f"[{tid}] Starting process_uncalculated_costs job")
    start_time = time.time()

    with task_db_session(db_session=db_session) as session:
        try:
            llm_usage_service = LlmUsageService(
                db_session=session, db_session_read=session
            )
            # Fetch up to 1000 uncalculated rows to avoid long transactions
            uncalculated_logs = llm_usage_service.get_pending_cost_calculations(
                limit=1000
            )

            if not uncalculated_logs:
                end_time = time.time()
                latency_ms = int((end_time - start_time) * 1000)
                logger.info(
                    f"[{tid}] No uncalculated costs found. (latency: {latency_ms}ms)"
                )
                return "No rows to update."

            updated_count = 0
            for log in uncalculated_logs:
                prompt_price, completion_price = get_model_prices(
                    str(log.service_name), str(log.model)
                )
                # Calculate cost based on tokens.
                prompt_cost = (log.prompt_tokens or 0) * prompt_price
                completion_cost = (log.completion_tokens or 0) * completion_price
                total_cost = prompt_cost + completion_cost
                logger.debug(
                    f"[{tid}] Log {log.id} - Prompt: {prompt_price}, Completion: {completion_price}, Total: {total_cost}"
                )
                log.cost = Decimal(str(total_cost))
                log.cost_calculated = True

                updated_log = LlmUsageLogUpdate(
                    cost=Decimal(str(total_cost)), cost_calculated=True
                )

                llm_usage_service.update_llm_usage_log(log.id, updated_log)

                updated_count += 1

            end_time = time.time()
            latency_ms = int((end_time - start_time) * 1000)
            logger.info(
                f"[{tid}] Successfully calculated and updated costs for {updated_count} logs. (latency: {latency_ms}ms)"
            )
            return f"Updated {updated_count} rows."
        except Exception as e:
            end_time = time.time()
            latency_ms = int((end_time - start_time) * 1000)
            logger.error(
                f"[{tid}] Error calculating costs: {e} (latency: {latency_ms}ms)"
            )
            raise


@celery_app.task(
    bind=True, name="app.modules.llm_usage.tasks.process_uncalculated_costs"
)
def process_uncalculated_costs(self=None):
    """
    Periodically checks for usage logs that don't have cost calculated.
    Fetches the price for each model and updates the cost.
    """
    task_id = (
        getattr(getattr(self, "request", None), "id", None) or f"bg-{uuid.uuid4()}"
    )
    return execute_process_uncalculated_costs_job(task_id=task_id)


def dispatch_process_uncalculated_costs(
    background_tasks: BackgroundTasks | None = None,
) -> tuple[str, bool]:
    """
    Dispatch process_uncalculated_costs to Celery if online, or fallback to background_tasks.
    """
    return dispatch_task(
        process_uncalculated_costs,
        execute_process_uncalculated_costs_job,
        background_tasks=background_tasks,
        task_name="process_uncalculated_costs",
        task_id_prefix="cost-bg-",
    )
