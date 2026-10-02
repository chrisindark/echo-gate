import logging
import os

from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)


class CrossEncoderVerifierService:
    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L6-v2"):
        self.hf_home = os.getenv("HF_HOME", "./.model_cache")
        # Initialize the cross-encoder model for query-to-answer relevance
        self.model = CrossEncoder(
            model_name,
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
