import logging
import math
import os
from typing import Any
from app.core.config import RERANKER_MODEL_NAME

from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)


class RerankerService:
    def __init__(
        self,
        model_name: str = RERANKER_MODEL_NAME,
    ):
        self.hf_home = os.getenv("HF_HOME", "./.model_cache")
        self.model = CrossEncoder(
            model_name, local_files_only=True, cache_folder=self.hf_home
        )

    def _sigmoid(self, x: float) -> float:
        try:
            return 1 / (1 + math.exp(-x))
        except OverflowError:
            return 0.0 if x < 0 else 1.0

    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        request_model: str | None = None,
        request_service: str | None = None,
        request_intent: str | None = None,
        request_temperature: float = 0.0,
        request_entities: list[str] = None,
        user_query: str | None = None,
        system_query: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            if not candidates:
                return []

            request_entities = request_entities or []
            request_entity_set = set(request_entities)

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

                # Normalize cross-encoder score via sigmoid
                base_score = self._sigmoid(float(score))

                # Compute penalties and boosts
                penalty_model = 0.05 if payload.get("model") != request_model else 0.0
                penalty_service = (
                    0.02 if payload.get("service_name") != request_service else 0.0
                )

                # Fallback to general query if missing
                candidate_intent = (
                    payload.get("metadata", {}).get("intent") or "general_query"
                )
                penalty_intent = 0.15 if candidate_intent != request_intent else 0.0

                candidate_temperature = payload.get("metadata", {}).get(
                    "temperature", 0.0
                )
                penalty_temperature = (
                    abs(candidate_temperature - request_temperature) * 0.1
                )

                # Entity Boost / Penalty
                candidate_entities = set(payload.get("entities", []))

                # # logic to allow close entities for more matches to surface
                # if request_entity_set and candidate_entities:
                #     intersection = request_entity_set.intersection(candidate_entities)
                #     # Jaccard index style or simple overlap ratio
                #     overlap_ratio = len(intersection) / max(len(request_entity_set), 1)
                #     boost_entities = overlap_ratio * 0.1
                # elif not request_entity_set and not candidate_entities:
                #     boost_entities = 0.05
                boost_entities = 0.0
                penalty_entities = 0.0

                if request_entity_set == candidate_entities:
                    if request_entity_set:
                        boost_entities = 0.1
                    else:
                        boost_entities = (
                            0.05  # small boost for matching lack of entities
                        )
                else:
                    penalty_entities = 0.4

                candidate_system_prompt = payload.get("system_prompt")
                penalty_system_prompt = 0.0
                if system_query != candidate_system_prompt:
                    if system_query and candidate_system_prompt:
                        set1 = set(system_query.split())
                        set2 = set(candidate_system_prompt.split())
                        if set1 or set2:
                            jaccard = len(set1.intersection(set2)) / len(set1.union(set2))
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
                    "penalty_system_prompt": penalty_system_prompt,
                    "boost_entities": boost_entities,
                }
                reranked.append(result)

            reranked.sort(
                key=lambda x: x["rerank_score"],
                reverse=True,
            )

            return reranked
        except Exception as e:
            logger.error(f"Error in reranking: {e}")
            return []
