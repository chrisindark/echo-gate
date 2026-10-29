import logging
import os

from app.core.config import config
from app.core.logger import log_latency
from app.modules.inference.inference_client import (
    InferenceClient,
    InferenceConfigError,
    InferenceUnavailable,
)

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Dense embeddings from the inference engine, plus local sparse BM25.

    Dense vectors always come from `inference-platform/` over HTTP; this process
    loads no transformer weights, so the gateway image carries no torch. There is
    deliberately no in-process or Ollama fallback: a missing engine is a startup
    failure (`verify()`), and a mid-life outage degrades the cache rather than
    being served by a second, differently-behaving code path.

    Sparse BM25 stays in-process. It is a tokenizer pass costing ~1ms, and its
    vectors are coupled to what is already stored in Qdrant -- serving it
    remotely would risk breaking hybrid search to save nothing.
    """

    def __init__(self, inference_client: InferenceClient | None = None) -> None:
        self.hf_home = os.getenv("HF_HOME", "./.model_cache")
        self.bm25_model_path = os.getenv("BM25_MODEL_PATH", "")
        # fastembed downloads two different things and keeps them in two
        # different caches: `cache_folder` is the Hugging Face hub cache, while
        # the ONNX artifact lives in `cache_dir`. Passing only the former leaves
        # the artifact in the library default (a temp directory), which is empty
        # on every container start -- so the model has to be baked into an
        # explicit, image-stable path.
        self.bm25_cache_dir = os.getenv("FASTEMBED_CACHE_PATH") or os.path.join(
            self.hf_home, "fastembed"
        )
        # Allow the first run to fetch the model when the cache is cold, unless a
        # deployment pins it (the container sets BM25_LOCAL_FILES_ONLY=true, since
        # the model is baked in and must never be downloaded at runtime).
        self.bm25_local_files_only = (
            os.getenv("BM25_LOCAL_FILES_ONLY", "false").lower() == "true"
        )

        self.inference = inference_client or InferenceClient()
        if not self.inference.enabled:
            raise InferenceConfigError(
                "Dense embeddings need the inference engine: INFERENCE_BASE_URL is "
                "unset and there is no in-process fallback any more (see "
                "docs/DEVELOPMENT.md)."
            )
        self.embedding_dimension = config.INFERENCE_EMBEDDING_DIM
        logger.info(
            "Dense embeddings delegated to the inference platform at %s (dim=%s)",
            self.inference.url_for("embed"),
            self.embedding_dimension,
        )

        logger.info("Loading sparse embedding model (BM25)...")
        from fastembed import SparseTextEmbedding

        self.sparse_model = SparseTextEmbedding(
            model_name="Qdrant/bm25",
            cache_dir=self.bm25_cache_dir,
            local_files_only=self.bm25_local_files_only,
            specific_model_path=self.bm25_model_path,
            cache_folder=self.hf_home,
        )
        logger.info(
            "Sparse BM25 ready (cache_dir=%s, local_files_only=%s)",
            self.bm25_cache_dir,
            self.bm25_local_files_only,
        )

    @property
    def model_name(self) -> str:
        """Identity of the dense model, as recorded on every cache point.

        The weights live in the inference engine, so the name comes from config:
        `InferenceClient.verify()` refuses to start when the engine serves a
        different model, which keeps this value truthful. It is also the value
        written to the Qdrant `embedding_model` payload field and used as a search
        filter, so a swap quarantines old vectors instead of mixing two spaces.
        """
        return config.INFERENCE_EMBED_MODEL or config.EMBEDDING_MODEL_NAME

    # -- dense -------------------------------------------------------------

    @log_latency()
    def get_embedding(self, text: str) -> list[float]:
        """Blocking embedding. For scripts and other off-loop callers only.

        Coroutines must use `get_embedding_async`: calling this from a coroutine
        blocks the event loop for a full network round trip.
        """
        return self.inference.embed_sync([text])[0]

    @log_latency()
    async def get_embedding_async(self, text: str) -> list[float]:
        """Async embedding.

        Raises `InferenceUnavailable` when the engine is unreachable. Callers on
        the cache path catch that and fall through to the provider; it must never
        surface to the API client.
        """
        return await self.inference.embed_one(text)

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
