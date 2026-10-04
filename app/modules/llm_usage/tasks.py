import logging
import time
from decimal import Decimal


from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.core.pricing import get_model_prices
from app.modules.llm_usage.llm_usage_schema import LlmUsageLogUpdate
from app.modules.llm_usage.llm_usage_service import LlmUsageService

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

    with SessionLocal() as db_session:
        try:
            llm_usage_service = LlmUsageService(
                db_session=db_session, db_session_read=db_session
            )
            # Fetch up to 1000 uncalculated rows to avoid long transactions
            uncalculated_logs = llm_usage_service.get_pending_cost_calculations(
                limit=1000
            )

            if not uncalculated_logs:
                end_time = time.time()
                latency_ms = int((end_time - start_time) * 1000)
                logger.info(
                    f"[{task_id}] No uncalculated costs found. (latency: {latency_ms}ms)"
                )
                return "No rows to update."

            updated_count = 0
            for log in uncalculated_logs:
                prompt_price, completion_price = get_model_prices(
                    str(log.service_name), str(log.model)
                )
                # Calculate cost based on tokens.
                # cache_read_tokens are usually billed at a discount in actual APIs (like OpenAI, Anthropic),
                # but for this MVP, we just use standard prompt and completion tokens.
                prompt_cost = (log.prompt_tokens or 0) * prompt_price
                completion_cost = (log.completion_tokens or 0) * completion_price
                total_cost = prompt_cost + completion_cost
                logger.debug(
                    f"[{task_id}] Log {log.id} - Prompt: {prompt_price}, Completion: {completion_price}, Total: {total_cost}"
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
                f"[{task_id}] Successfully calculated and updated costs for {updated_count} logs. (latency: {latency_ms}ms)"
            )
            return f"Updated {updated_count} rows."
        except Exception as e:
            end_time = time.time()
            latency_ms = int((end_time - start_time) * 1000)
            logger.error(
                f"[{task_id}] Error calculating costs: {e} (latency: {latency_ms}ms)"
            )
            raise
