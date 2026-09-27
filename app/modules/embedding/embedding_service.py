import os
import logging
import httpx
from typing import List, Optional
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

class EmbeddingService:
    def __init__(self, use_ollama: bool = True, model_name: str = 'nomic-embed-text:latest') -> None:
        self.use_ollama = use_ollama
        self.model_name = model_name
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        
        if self.use_ollama:
            logger.info(f"Using Ollama for embeddings with model: {self.model_name}")
            # nomic-embed-text dimension is 768
            self.embedding_dimension = 768 
        else:
            logger.info(f"Loading local embedding model: {model_name}")
            self.model: SentenceTransformer = SentenceTransformer(model_name)
            self.embedding_dimension = self.model.get_sentence_embedding_dimension() or 384
            logger.info(f"Model loaded. Dimension: {self.embedding_dimension}")

    def get_embedding(self, text: str) -> List[float]:
        """
        Synchronous fallback (mostly for local SentenceTransformer).
        """
        if self.use_ollama:
            # We recommend using get_embedding_async for network calls
            with httpx.Client() as client:
                response = client.post(
                    f"{self.ollama_url}/api/embeddings",
                    json={"model": self.model_name, "prompt": text},
                    timeout=30.0
                )
            response.raise_for_status()
            return response.json()["embedding"]
        else:
            return self.model.encode(text).tolist()

    async def get_embedding_async(self, text: str) -> List[float]:
        """
        Asynchronous generation of embedding.
        """
        if self.use_ollama:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.ollama_url}/api/embeddings",
                    json={"model": self.model_name, "prompt": text},
                    timeout=30.0
                )
                response.raise_for_status()
                return response.json()["embedding"]
        else:
            # Note: sentence-transformers encode is blocking
            return self.model.encode(text).tolist()
