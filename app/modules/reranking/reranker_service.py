import logging
from typing import Any

from app.core.logger import log_latency
from app.modules.verifiers.cross_encoder_service import CrossEncoderService
from app.modules.verifiers.instruction_service import InstructionVerifier

logger = logging.getLogger(__name__)


class RerankerService:
    def __init__(
        self,
        cross_encoder_service: CrossEncoderService,
        instruction_service: InstructionVerifier,
    ):
        self.cross_encoder_service = cross_encoder_service
        self.instruction_service = instruction_service

    def verify(
        self, query: str, user_query: str | None, candidates: list[dict[str, Any]]
    ) -> list[float]:
        pairs = []
        for candidate in candidates:
            payload = candidate.get("payload") or {}
            candidate_user_prompt = payload.get("user_prompt")
            if user_query and candidate_user_prompt:
                pairs.append((user_query, candidate_user_prompt))
            else:
                prompt = payload.get("prompt")
                if not prompt:
                    raise KeyError(
                        f"Candidate payload missing prompt: {candidate.get('id', 'unknown')}"
                    )
                pairs.append((query, prompt))

        return self.cross_encoder_service.rerank_predict(pairs)

    @log_latency()
    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        request_model: str | None = None,
        request_service: str | None = None,
        request_intent: str | None = None,
        request_temperature: float = 0.0,
        request_entities: list[str] | None = None,
        request_core_operation: str | None = None,
        request_core_subject: str | None = None,
        request_subject_modifier: str | None = None,
        request_action_modifier: str | None = None,
        user_query: str | None = None,
        system_query: str | None = None,
    ) -> list[dict[str, Any]]:
        logger.debug(f"request model: {request_model}")
        logger.debug(f"request service: {request_service}")
        logger.debug(f"request intent: {request_intent}")
        logger.debug(f"request temperature: {request_temperature}")
        logger.debug(f"request entities: {request_entities}")
        logger.debug(f"request core operation: {request_core_operation}")
        logger.debug(f"request core subject: {request_core_subject}")
        logger.debug(f"request subject modifier: {request_subject_modifier}")
        logger.debug(f"request action modifier: {request_action_modifier}")
        logger.debug(f"user query: {user_query}")
        logger.debug(f"system query: {system_query}")
        try:
            if not candidates:
                return []

            request_entities = request_entities or []
            request_entity_set = set(request_entities)
            request_core_operation = request_core_operation or ""
            request_core_subject = request_core_subject or ""
            request_subject_modifier = request_subject_modifier or ""
            request_action_modifier = request_action_modifier or ""

            reranked = []

            for candidate in candidates:
                try:
                    try:
                        c_text = (
                            candidate.get("payload", {})
                            .get("response", {})
                            .get("choices", [])[0]
                            .get("message", {})
                            .get("content", "")
                        )
                    except Exception:
                        logger.error(
                            f"Failed to extract response from candidate: {candidate}"
                        )
                        c_text = ""
                    # candidate_texts.append(c_text or "")

                    # deterministically verify json output only and apply penalty if not json
                    instruction_scores = self.instruction_service.verify_instruction(
                        user_query or query, [c_text], json_only=True
                    )
                    logger.debug(f"instruction_scores: {instruction_scores}")
                    instruction_score = instruction_scores[0]

                    scores = self.verify(query, user_query, [candidate])
                    logger.debug(f"reranker_scores: {scores}")
                    if not scores:
                        continue
                    score = scores[0]

                    payload = candidate.get("payload") or {}
                    logger.debug(f"candidate payload: {payload}")

                    # sentence-transformers CrossEncoder for bge-reranker already outputs probabilities in [0, 1]
                    base_score = float(score)

                    # Compute penalties and boosts
                    penalty_model = 0.0
                    candidate_model = payload.get("model")
                    logger.debug(f"candidate model: {candidate_model}")
                    if candidate_model != request_model:
                        penalty_model = 0.05
                        logger.debug(
                            f"Applied model penalty: req={request_model}, cand={payload.get('model')}"
                        )
                    else:
                        logger.debug(
                            f"No model penalty applied: req={request_model}, cand={payload.get('model')}"
                        )

                    penalty_service = 0.0
                    candidate_service_name = payload.get("service_name")
                    logger.debug(f"candidate service_name: {candidate_service_name}")
                    if candidate_service_name != request_service:
                        penalty_service = 0.02
                        logger.debug(
                            f"Applied service penalty: req={request_service}, cand={payload.get('service_name')}"
                        )
                    else:
                        penalty_service = 0.0
                        logger.debug(
                            f"No service penalty applied: req={request_service}, cand={payload.get('service_name')}"
                        )

                    # Fallback to general query if missing
                    candidate_intent = payload.get("intent") or "general_query"
                    logger.debug(f"candidate intent: {candidate_intent}")
                    penalty_intent = 0.0
                    if candidate_intent != request_intent:
                        penalty_intent = 0.15
                        logger.debug(
                            f"Applied intent penalty: req={request_intent}, cand={candidate_intent}"
                        )
                    else:
                        penalty_intent = 0.0
                        logger.debug(
                            f"No intent penalty applied: req={request_intent}, cand={candidate_intent}"
                        )

                    penalty_operation = 0.0
                    candidate_core_operation = payload.get("core_operation", "")
                    logger.debug(
                        f"candidate core_operation: {candidate_core_operation}"
                    )
                    if (request_core_operation or "").lower() != (
                        candidate_core_operation or ""
                    ).lower():
                        penalty_operation = 0.25
                        logger.debug(
                            f"Applied core_operation penalty: req={request_core_operation}, cand={candidate_core_operation}"
                        )
                    else:
                        logger.debug(
                            f"No core_operation penalty applied: req={request_core_operation}, cand={candidate_core_operation}"
                        )

                    penalty_subject = 0.0
                    candidate_core_subject = payload.get("core_subject", "")
                    logger.debug(f"candidate core_subject: {candidate_core_subject}")
                    if (request_core_subject or "").lower() != (
                        candidate_core_subject or ""
                    ).lower():
                        penalty_subject = 0.25
                        logger.debug(
                            f"Applied core_subject penalty: req={request_core_subject}, cand={candidate_core_subject}"
                        )
                    else:
                        logger.debug(
                            f"No core_subject penalty applied: req={request_core_subject}, cand={candidate_core_subject}"
                        )

                    penalty_subject_modifier = 0.0
                    candidate_subject_modifier = payload.get("subject_modifier", "")
                    logger.debug(
                        f"candidate subject_modifier: {candidate_subject_modifier}"
                    )
                    if (request_subject_modifier or "").lower() != (
                        candidate_subject_modifier or ""
                    ).lower():
                        penalty_subject_modifier = 0.25
                        logger.debug(
                            f"Applied subject_modifier penalty: req={request_subject_modifier}, cand={candidate_subject_modifier}"
                        )
                    else:
                        logger.debug(
                            f"No subject_modifier penalty applied: req_neg={request_subject_modifier}, cand={candidate_subject_modifier}"
                        )

                    penalty_action_modifier = 0.0
                    candidate_action_modifier = payload.get("action_modifier", "")
                    logger.debug(
                        f"candidate action_modifier: {candidate_action_modifier}"
                    )
                    if (request_action_modifier or "").lower() != (
                        candidate_action_modifier or ""
                    ).lower():
                        penalty_action_modifier = 0.25
                        logger.debug(
                            f"Applied action_modifier penalty: req={request_action_modifier}, cand={candidate_action_modifier}"
                        )
                    else:
                        logger.debug(
                            f"No action_modifier penalty applied: req_neg={request_action_modifier}, cand={candidate_action_modifier}"
                        )

                    candidate_temperature = payload.get("temperature")
                    if candidate_temperature is None:
                        candidate_temperature = 0.0
                    else:
                        try:
                            candidate_temperature = float(candidate_temperature)
                        except (ValueError, TypeError):
                            candidate_temperature = 0.0

                    logger.debug(f"candidate temperature: {candidate_temperature}")
                    req_temp = (
                        request_temperature if request_temperature is not None else 0.0
                    )
                    penalty_temperature = abs(candidate_temperature - req_temp) * 0.1

                    # Entity Boost / Penalty
                    candidate_entities = set(payload.get("entities") or [])
                    logger.debug(f"candidate entities: {candidate_entities}")

                    boost_entities = 0.0
                    penalty_entities = 0.0

                    if request_entity_set == candidate_entities:
                        if request_entity_set:
                            boost_entities = 0.1
                    else:
                        penalty_entities = 0.4

                    # Evaluate system prompt difference
                    candidate_system_prompt = payload.get("system_prompt")
                    penalty_system_prompt = 0.0
                    if system_query != candidate_system_prompt:
                        if system_query and candidate_system_prompt:
                            set1 = set(system_query.split())
                            set2 = set(candidate_system_prompt.split())
                            if set1 or set2:
                                jaccard = len(set1.intersection(set2)) / len(
                                    set1.union(set2)
                                )
                                penalty_system_prompt = 0.5 * (1.0 - jaccard)
                        elif system_query or candidate_system_prompt:
                            penalty_system_prompt = 0.5

                    # Evaluate instruction following penalty
                    penalty_instruction = 0
                    penalty_instruction = 0.5 * (1.0 - instruction_score)
                    if penalty_instruction > 0:
                        logger.debug(
                            f"Applied instruction penalty: {penalty_instruction}"
                        )

                    # Compute final cache confidence score
                    final_score = (
                        base_score
                        - penalty_model
                        - penalty_service
                        - penalty_intent
                        - penalty_temperature
                        - penalty_entities
                        - penalty_operation
                        - penalty_subject
                        - penalty_subject_modifier
                        - penalty_action_modifier
                        - penalty_system_prompt
                        - penalty_instruction
                        + boost_entities
                    )
                    # Clamp between 0 and 1
                    final_score = max(0.0, min(1.0, final_score))

                    result = candidate.copy()
                    result["raw_cross_encoder_score"] = float(score)
                    result["base_normalized_score"] = base_score
                    result["rerank_score"] = final_score

                    # attach details for debugging/cache_info
                    result["score_breakdown"] = {
                        "base": base_score,
                        "penalty_model": penalty_model,
                        "penalty_service": penalty_service,
                        "penalty_intent": penalty_intent,
                        "penalty_temperature": penalty_temperature,
                        "penalty_entities": penalty_entities,
                        "penalty_operation": penalty_operation,
                        "penalty_subject": penalty_subject,
                        "penalty_subject_modifier": penalty_subject_modifier,
                        "penalty_action_modifier": penalty_action_modifier,
                        "penalty_system_prompt": penalty_system_prompt,
                        "penalty_instruction": penalty_instruction,
                        "boost_entities": boost_entities,
                    }
                    reranked.append(result)
                except Exception:
                    logger.exception(
                        f"Error reranking candidate {candidate.get('id', 'unknown')}"
                    )
                    continue

            reranked.sort(
                key=lambda x: x["rerank_score"],
                reverse=True,
            )

            return reranked
        except Exception:
            logger.exception("Error in reranking")
            return []
