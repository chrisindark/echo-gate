import logging
from typing import Any

from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)


class RerankerService:
    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
    ):
        try:
            self.model = CrossEncoder(model_name, local_files_only=True)
        except Exception:
            logger.info(f"Model {model_name} not found locally, downloading...")
            self.model = CrossEncoder(model_name)

    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        try:
            if not candidates:
                return []

            pairs = [
                (query, candidate["payload"]["prompt"]) for candidate in candidates
            ]

            scores = self.model.predict(pairs)

            reranked = []

            for candidate, score in zip(candidates, scores):
                result = candidate.copy()
                result["rerank_score"] = float(score)
                reranked.append(result)

            reranked.sort(
                key=lambda x: x["rerank_score"],
                reverse=True,
            )

            return reranked
        except Exception as e:
            logger.error(f"Error in reranking: {e}")
            return []
