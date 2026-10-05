import asyncio
import logging
import time

from app.core.celery_app import celery_app
from app.core.config import config
from app.core.database import SessionLocal
from app.modules.gateway_requests import (
    EvaluationStatus,
    GatewayRequestLog,
    GatewayRequestLogUpdate,
)
from app.modules.gateway_requests.gateway_requests_service import GatewayRequestsService
from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.verifiers.cross_encoder_service import CrossEncoderService
from app.modules.verifiers.instruction_service import InstructionVerifier
from app.modules.verifiers.judge_service import JudgeService

logger = logging.getLogger(__name__)


def _prepare_prompts(request: GatewayRequestLog) -> tuple[str, str]:
    prompt_text = request.query_text

    if "|" in prompt_text:
        # The first part is usually "model:<model_name>|"
        # We split by the first "|" to get the messages part.
        _, messages_part = prompt_text.split("|", 1)
    else:
        messages_part = prompt_text

    messages_raw = messages_part.split("|\n")

    system_messages = []
    user_messages = []

    for msg in messages_raw:
        if msg.startswith("system:"):
            system_messages.append(msg.split(":", 1)[1].strip())
        elif msg.startswith("user:"):
            user_messages.append(msg.split(":", 1)[1].strip())

    system_raw_prompt = "\n".join(system_messages)
    user_raw_prompt = "\n".join(user_messages)

    # Fallback if no user messages found, just use the entire raw string
    if not user_raw_prompt:
        user_raw_prompt = messages_part.strip()

    return system_raw_prompt, user_raw_prompt


async def _evaluate_single_request(
    gateway_request: GatewayRequestLog,
    gateway_service: GatewayRequestsService,
    judge_service: JudgeService,
    task_id: str,
) -> None:
    logger.info(f"[{task_id}] Evaluating request {gateway_request.id}...")

    # Update status to IN_PROGRESS so it isn't picked up by another worker concurrently
    gateway_service.update_evaluation(
        gateway_request.id,
        GatewayRequestLogUpdate(evaluation_status=EvaluationStatus.IN_PROGRESS),
    )

    _, user_prompt_text = _prepare_prompts(gateway_request)
    # 2. LLM Judge Score
    llm_scores = await judge_service.evaluate(
        user_prompt_text, gateway_request.response_text
    )
    logger.debug(f"llm_scores: {llm_scores}")

    rel_score = llm_scores.get("llm_relevance_score")
    con_score = llm_scores.get("llm_contradiction_score")
    ins_score = llm_scores.get("llm_instruction_score")

    # Determine false positive
    # High contradiction or low relevance means it's a false positive
    is_false_positive = False
    if rel_score is not None and rel_score < 3:
        is_false_positive = True
    if con_score is not None and con_score > 0:
        is_false_positive = True

    # Update Database
    update_data = GatewayRequestLogUpdate(
        evaluation_status=EvaluationStatus.EVALUATED,
        llm_relevance_score=float(rel_score) if rel_score is not None else None,
        llm_contradiction_score=float(con_score) if con_score is not None else None,
        llm_instruction_score=float(ins_score) if ins_score is not None else None,
        is_false_positive=is_false_positive,
    )

    gateway_service.update_evaluation(gateway_request.id, update_data)


async def _evaluate_batch_async(task_id: str):
    start_time = time.time()

    with SessionLocal() as db_session:
        try:
            gateway_service = GatewayRequestsService(
                db_session=db_session, db_session_read=db_session
            )
            cross_encoder_service = CrossEncoderService()
            llm_provider_service = LlmProviderService()
            instruction_service = InstructionVerifier()
            judge_service = JudgeService(
                llm_provider_service=llm_provider_service,
                cross_encoder_service=cross_encoder_service,
                instruction_service=instruction_service,
            )

            # Fetch pending requests
            pending_requests = gateway_service.get_pending_evaluations(
                limit=config.EVALUATION_BATCH_SIZE
            )

            if not pending_requests:
                logger.info(f"[{task_id}] No pending evaluations found.")
                return 0

            evaluated_count = 0
            for gateway_request in pending_requests:
                await _evaluate_single_request(
                    gateway_request=gateway_request,
                    gateway_service=gateway_service,
                    judge_service=judge_service,
                    task_id=task_id,
                )
                evaluated_count += 1

            end_time = time.time()
            latency_ms = int((end_time - start_time) * 1000)
            logger.info(
                f"[{task_id}] Successfully evaluated {len(pending_requests)} logs. (latency: {latency_ms}ms)"
            )
            return evaluated_count
        except Exception as e:
            end_time = time.time()
            latency_ms = int((end_time - start_time) * 1000)
            logger.error(
                f"[{task_id}] Error evaluating logs: {e} (latency: {latency_ms}ms)"
            )
            raise


@celery_app.task(
    bind=True, name="app.modules.gateway_requests.tasks.evaluate_pending_requests"
)
def evaluate_pending_requests(self):
    task_id = self.request.id
    logger.info(f"[{task_id}] Starting evaluate_pending_requests task")
    start_time = time.time()

    # Use asyncio.run to execute the async evaluation function in the sync celery context
    evaluated_count = asyncio.run(_evaluate_batch_async(task_id))

    end_time = time.time()
    latency_ms = int((end_time - start_time) * 1000)
    logger.info(
        f"[{task_id}] Evaluated {evaluated_count} logs. (latency: {latency_ms}ms)"
    )
    return f"Evaluated {evaluated_count} requests."
