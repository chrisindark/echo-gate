import logging
import os
from typing import Any

from sentence_transformers import CrossEncoder

from app.core.config import config
from app.core.logger import log_latency

logger = logging.getLogger(__name__)


class RerankerService:
    def __init__(
        self,
        model_name: str | None = None,
    ):
        self.hf_home = os.getenv("HF_HOME", "./.model_cache")
        self.model = CrossEncoder(
            model_name or config.RERANKER_MODEL_NAME,
            local_files_only=True,
            cache_folder=self.hf_home,
        )

    def verify(self, query: str, candidates: list[str]) -> list[float]:
        if not candidates:
            return []

        pairs = [(query, candidate) for candidate in candidates]
        scores = self.model.predict(pairs)

        if isinstance(scores, float) or scores.ndim == 0:
            return [float(scores)]

        return [float(score) for score in scores]

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
        try:
            if not candidates:
                return []

            request_entities = request_entities or []
            request_entity_set = set(request_entities)
            request_core_operation = request_core_operation or ""
            request_core_subject = request_core_subject or ""
            request_subject_modifier = request_subject_modifier or ""
            request_action_modifier = request_action_modifier or ""

            pairs = []
            for candidate in candidates:
                candidate_user_prompt = candidate.get("payload", {}).get("user_prompt")
                if user_query and candidate_user_prompt:
                    pairs.append((user_query, candidate_user_prompt))
                else:
                    pairs.append((query, candidate["payload"]["prompt"]))

            scores = self.model.predict(pairs)

            reranked = []

            for candidate, score in zip(candidates, scores):
                payload = candidate.get("payload", {})

                # sentence-transformers CrossEncoder for bge-reranker already outputs probabilities in [0, 1]
                base_score = float(score)

                # Compute penalties and boosts
                penalty_model = 0.0
                if payload.get("model") != request_model:
                    penalty_model = 0.05
                    logger.debug(
                        f"Applied model penalty: req={request_model}, cand={payload.get('model')}"
                    )
                else:
                    logger.debug(
                        f"No model penalty applied: req={request_model}, cand={payload.get('model')}"
                    )

                penalty_service = 0.0
                if payload.get("service_name") != request_service:
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

                candidate_core_operation = payload.get("core_operation", "")
                penalty_operation = 0.0
                if (
                    request_core_operation
                    and candidate_core_operation
                    and request_core_operation.lower()
                    != candidate_core_operation.lower()
                ):
                    penalty_operation = 0.25
                    logger.debug(
                        f"Applied core_operation penalty: req={request_core_operation}, cand={candidate_core_operation}"
                    )
                else:
                    logger.debug(
                        f"No core_operation penalty applied: req={request_core_operation}, cand={candidate_core_operation}"
                    )

                candidate_core_subject = payload.get("core_subject", "")
                penalty_subject = 0.0
                if (
                    request_core_subject
                    and candidate_core_subject
                    and request_core_subject.lower() != candidate_core_subject.lower()
                ):
                    penalty_subject = 0.25
                    logger.debug(
                        f"Applied core_subject penalty: req={request_core_subject}, cand={candidate_core_subject}"
                    )
                else:
                    logger.debug(
                        f"No core_subject penalty applied: req={request_core_subject}, cand={candidate_core_subject}"
                    )

                candidate_subject_modifier = payload.get("subject_modifier", "")
                penalty_subject_modifier = 0.0
                if (
                    request_subject_modifier
                    and candidate_subject_modifier
                    and request_subject_modifier.lower()
                    != candidate_subject_modifier.lower()
                ):
                    penalty_subject_modifier = 0.25
                    logger.debug(
                        f"Applied subject_modifier penalty: req={request_subject_modifier}, cand={candidate_subject_modifier}"
                    )
                else:
                    logger.debug(
                        f"No subject_modifier penalty applied: req_neg={request_subject_modifier}, cand={candidate_subject_modifier}"
                    )

                candidate_action_modifier = payload.get("action_modifier", "")
                penalty_action_modifier = 0.0
                if (
                    request_action_modifier
                    and candidate_action_modifier
                    and request_action_modifier.lower()
                    != candidate_action_modifier.lower()
                ):
                    penalty_action_modifier = 0.25
                    logger.debug(
                        f"Applied action_modifier penalty: req={request_action_modifier}, cand={candidate_action_modifier}"
                    )
                else:
                    logger.debug(
                        f"No action_modifier penalty applied: req_neg={request_action_modifier}, cand={candidate_action_modifier}"
                    )

                candidate_temperature = payload.get("temperature", 0.0)
                penalty_temperature = (
                    abs(candidate_temperature - request_temperature) * 0.1
                )

                # Entity Boost / Penalty
                candidate_entities = set(payload.get("entities", []))

                boost_entities = 0.0
                penalty_entities = 0.0

                if request_entity_set == candidate_entities:
                    if request_entity_set:
                        boost_entities = 0.1
                    else:
                        boost_entities = 0.05
                else:
                    penalty_entities = 0.4

                # Detect explicit logical contradictions
                candidate_prompt = candidate.get("payload", {}).get("prompt", "")
                candidate_user_prompt = candidate.get("payload", {}).get(
                    "user_prompt", candidate_prompt
                )

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
                    "boost_entities": boost_entities,
                }
                reranked.append(result)

            reranked.sort(
                key=lambda x: x["rerank_score"],
                reverse=True,
            )

            return reranked
        except Exception:
            logger.exception("Error in reranking")
            return []
