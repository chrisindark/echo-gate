import logging
import os

import httpx
from fastembed import SparseTextEmbedding
from sentence_transformers import SentenceTransformer

from app.core.config import config
from app.core.logger import log_latency

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, use_ollama: bool = True, model_name: str | None = None) -> None:
        self.use_ollama = use_ollama
        self.model_name = model_name or config.EMBEDDING_MODEL_NAME
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.hf_home = os.getenv("HF_HOME", "./.model_cache")
        self.bm25_model_path = os.getenv("BM25_MODEL_PATH", "")

        logger.info("Loading sparse embedding model (BM25)...")
        self.sparse_model = SparseTextEmbedding(
            model_name="Qdrant/bm25",
            local_files_only=True,
            specific_model_path=self.bm25_model_path,
            cache_folder=self.hf_home,
        )

        if self.use_ollama:
            logger.info(f"Using Ollama for embeddings with model: {self.model_name}")
            # nomic-embed-text dimension is 768
            self.embedding_dimension = 768
            logger.info(f"Model loaded. Dimension: {self.embedding_dimension}")
        else:
            logger.info(f"Loading local embedding model: {model_name}")
            self.model: SentenceTransformer = SentenceTransformer(
                model_name, local_files_only=True, cache_folder=self.hf_home
            )
            self.embedding_dimension = self.model.get_embedding_dimension() or 384
            logger.info(f"Model loaded. Dimension: {self.embedding_dimension}")

    @log_latency()
    def get_embedding(self, text: str) -> list[float]:
        """
        Synchronous fallback (mostly for local SentenceTransformer).
        """
        if self.use_ollama:
            # We recommend using get_embedding_async for network calls
            with httpx.Client() as client:
                response = client.post(
                    f"{self.ollama_url}/api/embeddings",
                    json={"model": self.model_name, "prompt": text},
                    timeout=30.0,
                )
            response.raise_for_status()
            return response.json()["embedding"]
        else:
            return self.model.encode(text).tolist()

    @log_latency()
    async def get_embedding_async(self, text: str) -> list[float]:
        """
        Asynchronous generation of embedding.
        """
        if self.use_ollama:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.ollama_url}/api/embeddings",
                    json={"model": self.model_name, "prompt": text},
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.json()["embedding"]
        else:
            # Note: sentence-transformers encode is blocking
            return self.model.encode(text).tolist()

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
