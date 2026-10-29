import logging

from app.core.config import config
from app.modules.inference.inference_client import (
    InferenceClient,
    InferenceConfigError,
    InferenceUnavailable,
)

logger = logging.getLogger(__name__)

# The contract caps documents per request; longer batches are split so a large
# candidate set degrades into more round trips instead of a 413.
_MAX_BATCH = 256


class CrossEncoderService:
    """Reranking, relevance and NLI scoring, all served by the inference engine.

    Three separate models back the three roles, which is why sharing them across
    processes matters: each prefork child otherwise pays ~1.7GB. The `USE_*` flags
    survive as per-role toggles -- they do not switch to a local model, they make
    the role raise `InferenceUnavailable` so callers take their explicit fallback.

    Failure policy: `InferenceUnavailable` is raised, never swallowed, so every
    caller makes an explicit choice. The cache path treats it as "no candidates"
    and falls through to the provider; the operator endpoint returns 503.
    """

    def __init__(self, inference_client: InferenceClient | None = None):
        self.inference = inference_client or InferenceClient()
        if not self.inference.enabled:
            raise InferenceConfigError(
                "Cross-encoder roles need the inference engine: INFERENCE_BASE_URL is "
                "unset and there is no in-process fallback any more (see "
                "docs/DEVELOPMENT.md)."
            )
        logger.info(
            "Cross-encoder roles delegated to the inference platform "
            "(rerank=%s, nli=%s)",
            self.inference.url_for("rerank"),
            self.inference.url_for("nli"),
        )

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
        if not config.USE_RERANKER_MODEL:
            raise InferenceUnavailable("reranker role is disabled (USE_RERANKER_MODEL)")
        return self._rerank_pairs_remote(pairs)

    async def rerank_predict_async(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        if not config.USE_RERANKER_MODEL:
            raise InferenceUnavailable("reranker role is disabled (USE_RERANKER_MODEL)")
        return await self._rerank_pairs_remote_async(pairs)

    # -- relevance ---------------------------------------------------------

    def relevance_predict(self, query: str, candidates: list[str]) -> list[float]:
        if not candidates:
            return []
        if not config.USE_RELEVANCE_MODEL:
            raise InferenceUnavailable(
                "relevance role is disabled (USE_RELEVANCE_MODEL)"
            )
        scores: list[float] = []
        for chunk in self._chunks(candidates):
            scores.extend(self.inference.rerank_sync(query, chunk, role="relevance"))
        return scores

    async def relevance_predict_async(
        self, query: str, candidates: list[str]
    ) -> list[float]:
        if not candidates:
            return []
        if not config.USE_RELEVANCE_MODEL:
            raise InferenceUnavailable(
                "relevance role is disabled (USE_RELEVANCE_MODEL)"
            )
        scores: list[float] = []
        for chunk in self._chunks(candidates):
            scores.extend(await self.inference.rerank(query, chunk, role="relevance"))
        return scores

    # -- NLI ---------------------------------------------------------------

    def nli_predict(self, query: str, candidates: list[str]) -> list[list[float]]:
        if not candidates:
            return []
        if not config.USE_NLI_MODEL:
            raise InferenceUnavailable("NLI role is disabled (USE_NLI_MODEL)")
        results: list[list[float]] = []
        for chunk in self._chunks(candidates):
            pairs = [(query, candidate) for candidate in chunk]
            results.extend(self.inference.classify_sync(pairs))
        return results

    async def nli_predict_async(
        self, query: str, candidates: list[str]
    ) -> list[list[float]]:
        if not candidates:
            return []
        if not config.USE_NLI_MODEL:
            raise InferenceUnavailable("NLI role is disabled (USE_NLI_MODEL)")
        results: list[list[float]] = []
        for chunk in self._chunks(candidates):
            pairs = [(query, candidate) for candidate in chunk]
            results.extend(await self.inference.classify(pairs))
        return results


__all__ = ["CrossEncoderService", "InferenceUnavailable"]
