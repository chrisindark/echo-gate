import logging
import os

from sentence_transformers import CrossEncoder

from app.core.config import config

logger = logging.getLogger(__name__)


class CrossEncoderService:
    def __init__(
        self,
        reranker_model_name: str = config.RERANKER_MODEL_NAME,
        relevance_model_name: str = config.CROSS_ENCODER_RELEVANCE_MODEL,
        nli_model_name: str = config.CROSS_ENCODER_NLI_MODEL,
    ):
        self.hf_home = os.getenv("HF_HOME", "./.model_cache")
        logger.debug(f"HF_HOME: {self.hf_home}")
        self.reranker_model = None
        self.relevance_model = None
        self.nli_model = None

        # Initialize the reranker model for semantic cache matching
        if config.USE_RERANKER_MODEL:
            self.reranker_model = CrossEncoder(
                reranker_model_name,
                local_files_only=True,
                cache_folder=self.hf_home,
            )

        # Initialize the cross-encoder model for query-to-answer relevance
        if config.USE_RELEVANCE_MODEL:
            self.relevance_model = CrossEncoder(
                relevance_model_name,
                local_files_only=True,
                cache_folder=self.hf_home,
            )

        # Initialize the NLI model for contradiction detection
        if config.USE_NLI_MODEL:
            self.nli_model = CrossEncoder(
                nli_model_name,
                local_files_only=True,
                cache_folder=self.hf_home,
            )

    def rerank_predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        scores = self.reranker_model.predict(pairs)
        if isinstance(scores, float) or scores.ndim == 0:
            return [float(scores)]
        return [float(score) for score in scores]

    def relevance_predict(self, query: str, candidates: list[str]) -> list[float]:
        if not candidates:
            return []

        pairs = [(query, candidate) for candidate in candidates]
        scores = self.relevance_model.predict(pairs)

        if isinstance(scores, float) or scores.ndim == 0:
            return [float(scores)]

        return [float(score) for score in scores]

    def nli_predict(self, query: str, candidates: list[str]) -> list[list[float]]:
        if not candidates:
            return []

        pairs = [(query, candidate) for candidate in candidates]
        scores = self.nli_model.predict(pairs)

        if scores.ndim == 1:
            return [
                float(score) for score in scores
            ]  # 1D array of single scores if model outputs 1 value

        return scores.tolist()
