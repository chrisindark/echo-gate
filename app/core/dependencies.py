from typing import Optional


from app.modules.embedding.embedding_service import EmbeddingService
from app.modules.qdrant.qdrant_service import QdrantService
from app.modules.llm.llm_service import LlmService
from app.modules.redis.redis_service import RedisService

class DependencyContainer:
    _embedding_service: Optional[EmbeddingService] = None
    _qdrant_service: Optional[QdrantService] = None
    _llm_service: Optional[LlmService] = None
    _redis_service: Optional[RedisService] = None

    @classmethod
    def get_embedding_service(self) -> EmbeddingService:
        if self._embedding_service is None:
            self._embedding_service = EmbeddingService()
        return self._embedding_service

    @classmethod
    def get_qdrant_service(self) -> QdrantService:
        if self._qdrant_service is None:
            embedding_svc = self.get_embedding_service()
            self._qdrant_service = QdrantService(vector_size=embedding_svc.embedding_dimension)
        return self._qdrant_service

    @classmethod
    def get_redis_service(self) -> RedisService:
        if self._redis_service is None:
            self._redis_service = RedisService()
        return self._redis_service

    @classmethod
    def get_llm_service(self) -> LlmService:
        if self._llm_service is None:
            self._llm_service = LlmService(
                embedding_service=self.get_embedding_service(),
                qdrant_service=self.get_qdrant_service(),
                redis_service=self.get_redis_service()
            )
        return self._llm_service

# FastAPI Depends compatible functions
def get_embedding_service() -> EmbeddingService:
    return DependencyContainer.get_embedding_service()

def get_qdrant_service() -> QdrantService:
    return DependencyContainer.get_qdrant_service()

def get_redis_service() -> RedisService:
    return DependencyContainer.get_redis_service()

def get_llm_service() -> LlmService:
    return DependencyContainer.get_llm_service()
