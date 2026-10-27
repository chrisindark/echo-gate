import asyncio
import logging
import os

import httpx

from app.core.config import config
from app.core.logger import log_latency
from app.modules.inference.inference_client import (
    InferenceClient,
    InferenceUnavailable,
)

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Dense embeddings (remote or in-process) plus local sparse BM25.

    Three modes, in priority order:

    1. **Shared inference platform** (`INFERENCE_BASE_URL` set): dense vectors
       come from `inference-platform/` over HTTP and this process loads no
       torch weights at all.
    2. **Ollama embeddings** (legacy `use_ollama=True`): remote, 768-dim.
    3. **In-process SentenceTransformer** (default when neither is configured):
       unchanged behaviour for existing deployments.

    Sparse BM25 is always in-process. It is a tokenizer pass costing ~1ms, and
    its vectors are coupled to what is already stored in Qdrant — serving it
    remotely would risk breaking hybrid search to save nothing.
    """

    def __init__(
        self,
        use_ollama: bool = True,
        model_name: str | None = None,
        inference_client: InferenceClient | None = None,
    ) -> None:
        self.use_ollama = use_ollama
        self.model_name = model_name or config.EMBEDDING_MODEL_NAME
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.hf_home = os.getenv("HF_HOME", "./.model_cache")
        self.bm25_model_path = os.getenv("BM25_MODEL_PATH", "")

        self.inference: InferenceClient | None = None
        if inference_client is not None:
            self.inference = inference_client
        elif config.INFERENCE_ENABLED:
            self.inference = InferenceClient()

        logger.info("Loading sparse embedding model (BM25)...")
        from fastembed import SparseTextEmbedding

        self.sparse_model = SparseTextEmbedding(
            model_name="Qdrant/bm25",
            local_files_only=True,
            specific_model_path=self.bm25_model_path,
            cache_folder=self.hf_home,
        )

        self.model = None
        if self.inference is not None:
            self.embedding_dimension = config.INFERENCE_EMBEDDING_DIM
            logger.info(
                "Dense embeddings delegated to the inference platform at %s "
                "(dim=%s); no local transformer is loaded",
                self.inference.base_url,
                self.embedding_dimension,
            )
        elif self.use_ollama:
            logger.info(f"Using Ollama for embeddings with model: {self.model_name}")
            # nomic-embed-text dimension is 768
            self.embedding_dimension = 768
            logger.info(f"Model loaded. Dimension: {self.embedding_dimension}")
        else:
            logger.info(f"Loading local embedding model: {self.model_name}")
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer(
                self.model_name, local_files_only=True, cache_folder=self.hf_home
            )
            self.embedding_dimension = self.model.get_embedding_dimension() or 384
            logger.info(f"Model loaded. Dimension: {self.embedding_dimension}")

    @property
    def is_remote(self) -> bool:
        """True when dense vectors come from outside this process."""
        return self.inference is not None or self.use_ollama

    # -- dense -------------------------------------------------------------

    def _encode_local(self, text: str) -> list[float]:
        return self.model.encode(text).tolist()

    def _timestamp(self, text: str) -> list[float]:
        """Legacy Ollama embedding endpoint (768-dim)."""
        with httpx.Client() as client:
            response = client.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": self.model_name, "prompt": text},
                timeout=30.0,
            )
        response.raise_for_status()
        return response.json()["embedding"]

    @log_latency()
    def get_embedding(self, text: str) -> list[float]:
        """Blocking embedding. For scripts and other off-loop callers only.

        Coroutines must use `get_embedding_async`: calling this from a coroutine
        blocks the event loop for a full network round trip.
        """
        if self.inference is not None:
            return self.inference.embed_sync([text])[0]
        if self.use_ollama:
            return self._timestamp(text)
        return self._encode_local(text)

    @log_latency()
    async def get_embedding_async(self, text: str) -> list[float]:
        """Async embedding.

        Raises `InferenceUnavailable` when the engine is unreachable. Callers on
        the cache path catch that and fall through to the provider; it must never
        surface to the API client.
        """
        if self.inference is not None:
            return await self.inference.embed_one(text)
        if self.use_ollama:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.ollama_url}/api/embeddings",
                    json={"model": self.model_name, "prompt": text},
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.json()["embedding"]
        # Note: sentence-transformers encode is blocking
        return await asyncio.to_thread(self._encode_local, text)

    # -- sparse (always local) --------------------------------------------

    @log_latency()
    def get_sparse_embedding(self, text: str) -> dict[str, list]:
        """
        Generates sparse embeddings (BM25) using fastembed.
        Returns a dictionary with 'indices' and 'values'.
        """
        # sparse_model.embed returns a generator of SparseEmbedding objects
        # SparseEmbedding has .indices and .values
        embeddings = list(self.sparse_model.embed([text]))
        if not embeddings:
            return {"indices": [], "values": []}

        return {
            "indices": embeddings[0].indices.tolist(),
            "values": embeddings[0].values.tolist(),
        }


__all__ = ["EmbeddingService", "InferenceUnavailable"]
