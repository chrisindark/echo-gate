import asyncio
import logging
import os

from app.core.config import config
from app.modules.inference.inference_client import (
    InferenceClient,
    InferenceUnavailable,
)

logger = logging.getLogger(__name__)

# The contract caps documents per request; longer batches are split so a large
# candidate set degrades into more round trips instead of a 413.
_MAX_BATCH = 256


class CrossEncoderService:
    """Reranking, relevance and NLI scoring.

    Served either by the shared inference platform (`INFERENCE_BASE_URL`) or, when
    that is unset, by cross-encoders loaded into this process (legacy behaviour).
    Three separate models back the three roles, which is why sharing them across
    processes matters: each prefork child otherwise pays ~1.7GB.

    Failure policy: `InferenceUnavailable` is raised, never swallowed, so every
    caller makes an explicit choice. The cache path treats it as "no candidates"
    and falls through to the provider; the operator endpoint returns 503.
    """

    def __init__(
        self,
        reranker_model_name: str = config.RERANKER_MODEL_NAME,
        relevance_model_name: str = config.CROSS_ENCODER_RELEVANCE_MODEL,
        nli_model_name: str = config.CROSS_ENCODER_NLI_MODEL,
        inference_client: InferenceClient | None = None,
    ):
        self.hf_home = os.getenv("HF_HOME", "./.model_cache")
        self.reranker_model = None
        self.relevance_model = None
        self.nli_model = None

        self.inference: InferenceClient | None = None
        if inference_client is not None:
            self.inference = inference_client
        elif config.INFERENCE_ENABLED:
            self.inference = InferenceClient()

        if self.inference is not None:
            url_func = getattr(
                self.inference,
                "url_for",
                lambda role: getattr(self.inference, "base_url", ""),
            )
            logger.info(
                "Cross-encoder roles delegated to the inference platform (rerank=%s, classify=%s); "
                "no local transformer is loaded",
                url_func("rerank"),
                url_func("nli"),
            )
            return

        # torch is imported only on the in-process path so that a deployment
        # running against the inference platform never pays its import cost.
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - deployment shape
            raise RuntimeError(
                "Cross-encoder roles are configured in-process but "
                "sentence-transformers is not installed. Set INFERENCE_BASE_URL to "
                "serve them from the inference platform, or install the local-models "
                "extra: poetry install --extras local-models"
            ) from exc

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

    @property
    def is_remote(self) -> bool:
        return self.inference is not None

    # -- plumbing ----------------------------------------------------------

    @staticmethod
    def _group_by_query(
        pairs: list[tuple[str, str]],
    ) -> list[tuple[str, list[int], list[str]]]:
        """Split pairs into per-query documents, keeping input positions.

        The contract takes one query and a document list, while these services
        accept arbitrary pairs. Call sites today always pass a shared query, but
        grouping rather than asserting keeps that an implementation detail.
        """
        order: list[str] = []
        buckets: dict[str, list[int]] = {}
        documents: dict[str, list[str]] = {}
        for position, (query, document) in enumerate(pairs):
            if query not in buckets:
                buckets[query] = []
                documents[query] = []
                order.append(query)
            buckets[query].append(position)
            documents[query].append(document)
        return [(query, buckets[query], documents[query]) for query in order]

    def _chunks(self, items: list[str]) -> list[list[str]]:
        return [items[i : i + _MAX_BATCH] for i in range(0, len(items), _MAX_BATCH)]

    def _chunk_bucket(
        self, positions: list[int], documents: list[str]
    ) -> list[tuple[list[int], list[str]]]:
        """Split one query's bucket so no request exceeds the document cap."""
        return [
            (positions[i : i + _MAX_BATCH], documents[i : i + _MAX_BATCH])
            for i in range(0, len(positions), _MAX_BATCH)
        ]

    # -- rerank ------------------------------------------------------------

    def _rerank_pairs_remote(self, pairs: list[tuple[str, str]]) -> list[float]:
        scores = [0.0] * len(pairs)
        for query, positions, documents in self._group_by_query(pairs):
            for chunk_positions, chunk_documents in self._chunk_bucket(
                positions, documents
            ):
                chunk_scores = self.inference.rerank_sync(query, chunk_documents)
                for position, score in zip(chunk_positions, chunk_scores, strict=True):
                    scores[position] = score
        return scores

    async def _rerank_pairs_remote_async(
        self, pairs: list[tuple[str, str]]
    ) -> list[float]:
        scores = [0.0] * len(pairs)
        for query, positions, documents in self._group_by_query(pairs):
            for chunk_positions, chunk_documents in self._chunk_bucket(
                positions, documents
            ):
                chunk_scores = await self.inference.rerank(query, chunk_documents)
                for position, score in zip(chunk_positions, chunk_scores, strict=True):
                    scores[position] = score
        return scores

    def rerank_predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Blocking variant. Safe only off the event loop (`asyncio.to_thread`)."""
        if not pairs:
            return []
        if self.inference is not None:
            return self._rerank_pairs_remote(pairs)
        if self.reranker_model is None:
            raise InferenceUnavailable("reranker role is disabled")
        scores = self.reranker_model.predict(pairs)
        if isinstance(scores, float) or scores.ndim == 0:
            return [float(scores)]
        return [float(score) for score in scores]

    async def rerank_predict_async(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        if self.inference is not None:
            return await self._rerank_pairs_remote_async(pairs)
        return await asyncio.to_thread(self.rerank_predict, pairs)

    # -- relevance ---------------------------------------------------------

    def relevance_predict(self, query: str, candidates: list[str]) -> list[float]:
        if not candidates:
            return []
        if self.inference is not None:
            scores: list[float] = []
            for chunk in self._chunks(candidates):
                scores.extend(
                    self.inference.rerank_sync(query, chunk, role="relevance")
                )
            return scores
        if self.relevance_model is None:
            raise InferenceUnavailable("relevance role is disabled")

        pairs = [(query, candidate) for candidate in candidates]
        scores = self.relevance_model.predict(pairs)

        if isinstance(scores, float) or scores.ndim == 0:
            return [float(scores)]

        return [float(score) for score in scores]

    async def relevance_predict_async(
        self, query: str, candidates: list[str]
    ) -> list[float]:
        if not candidates:
            return []
        if self.inference is not None:
            scores: list[float] = []
            for chunk in self._chunks(candidates):
                scores.extend(
                    await self.inference.rerank(query, chunk, role="relevance")
                )
            return scores
        return await asyncio.to_thread(self.relevance_predict, query, candidates)

    # -- NLI ---------------------------------------------------------------

    def nli_predict(self, query: str, candidates: list[str]) -> list[list[float]]:
        if not candidates:
            return []
        if self.inference is not None:
            results: list[list[float]] = []
            for chunk in self._chunks(candidates):
                pairs = [(query, candidate) for candidate in chunk]
                results.extend(self.inference.classify_sync(pairs))
            return results
        if self.nli_model is None:
            raise InferenceUnavailable("NLI role is disabled")

        pairs = [(query, candidate) for candidate in candidates]
        scores = self.nli_model.predict(pairs)

        if scores.ndim == 1:
            return [
                float(score) for score in scores
            ]  # 1D array of single scores if model outputs 1 value

        return scores.tolist()

    async def nli_predict_async(
        self, query: str, candidates: list[str]
    ) -> list[list[float]]:
        if not candidates:
            return []
        if self.inference is not None:
            results: list[list[float]] = []
            for chunk in self._chunks(candidates):
                pairs = [(query, candidate) for candidate in chunk]
                results.extend(await self.inference.classify(pairs))
            return results
        return await asyncio.to_thread(self.nli_predict, query, candidates)


__all__ = ["CrossEncoderService", "InferenceUnavailable"]
