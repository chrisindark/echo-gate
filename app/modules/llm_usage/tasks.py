import logging
import time

from sqlalchemy.orm import Session

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.core.pricing import get_model_prices
from app.modules.llm_usage.llm_usage_model import LlmUsageLog

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True, name="app.modules.llm_usage.tasks.process_uncalculated_costs"
)
def process_uncalculated_costs(self):
    """
    Periodically checks for usage logs that don't have cost calculated.
    Fetches the price for each model and updates the cost.
    """
    task_id = self.request.id
    logger.info(f"[{task_id}] Starting process_uncalculated_costs task")
    start_time = time.time()

    db: Session = SessionLocal()
    try:
        # Fetch up to 1000 uncalculated rows to avoid long transactions
        uncalculated_logs = (
            db.query(LlmUsageLog)
            .filter(LlmUsageLog.cost_calculated == False)
            .limit(1000)
            .all()
        )

        if not uncalculated_logs:
            latency_ms = int((time.time() - start_time) * 1000)
            logger.info(
                f"[{task_id}] No uncalculated costs found. (latency: {latency_ms}ms)"
            )
            return "No rows to update."

        updated_count = 0
        for log in uncalculated_logs:
            prompt_price, completion_price = get_model_prices(
                log.service_name, log.model
            )

            # Calculate cost based on tokens.
            # cache_read_tokens are usually billed at a discount in actual APIs (like Anthropic),
            # but for this MVP, we just use standard prompt and completion tokens.
            prompt_cost = (log.prompt_tokens or 0) * prompt_price
            completion_cost = (log.completion_tokens or 0) * completion_price
            total_cost = prompt_cost + completion_cost

            log.cost = total_cost
            log.cost_calculated = True
            updated_count += 1

        db.commit()
        latency_ms = int((time.time() - start_time) * 1000)
        logger.info(
            f"[{task_id}] Successfully calculated and updated costs for {updated_count} logs. (latency: {latency_ms}ms)"
        )
        return f"Updated {updated_count} rows."
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        logger.error(
            f"[{task_id}] Error calculating costs: {e} (latency: {latency_ms}ms)"
        )
        db.rollback()
        raise
    finally:
        db.close()
