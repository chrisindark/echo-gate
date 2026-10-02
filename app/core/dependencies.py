from app.core.config import config
from app.core.database import get_db, get_db_read
from app.modules.embedding.embedding_service import EmbeddingService
from app.modules.intent_classifier.entity_extractor_service import (
    EntityExtractorService,
)
from app.modules.intent_classifier.intent_classifier_service import (
    IntentClassifierService,
)
from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.llm.llm_router_service import LlmRouterService
from app.modules.llm_quota.llm_quota_service import LlmQuotaService
from app.modules.llm_usage.llm_usage_service import LlmUsageService
from app.modules.qdrant.qdrant_service import QdrantService
from app.modules.redis.redis_service import RedisService
from app.modules.reranking.reranker_service import RerankerService
from app.modules.verifiers.cross_encoder_verifier_service import (
    CrossEncoderVerifierService,
)


class DependencyContainer:
    _embedding_service: EmbeddingService | None = None
    _qdrant_service: QdrantService | None = None
    _llm_provider_service: LlmProviderService | None = None
    _llm_router_service: LlmRouterService | None = None
    _redis_service: RedisService | None = None
    _reranker_service: RerankerService | None = None
    _intent_classifier_service: IntentClassifierService | None = None
    _entity_extractor_service: EntityExtractorService | None = None
    _llm_usage_service: LlmUsageService | None = None
    _llm_quota_service: LlmQuotaService | None = None
    _cross_encoder_verifier_service: CrossEncoderVerifierService | None = None

    @classmethod
    async def initialize(cls) -> None:
        cls.get_embedding_service()
        cls.get_qdrant_service()
        await cls.get_redis_service()
        cls.get_llm_provider_service()
        await cls.get_intent_classifier_service()
        cls.get_entity_extractor_service()
        await cls.get_llm_router_service()
        cls.get_llm_usage_service()
        cls.get_llm_quota_service()
        cls.get_cross_encoder_verifier_service()

    @classmethod
    async def shutdown(cls) -> None:
        try:
            if cls._redis_service is not None:
                await cls._redis_service.close()
        finally:
            if cls._qdrant_service is not None:
                cls._qdrant_service.close()
            cls._embedding_service = None
            cls._qdrant_service = None
            cls._llm_provider_service = None
            cls._llm_router_service = None
            cls._redis_service = None
            cls._reranker_service = None
            cls._intent_classifier_service = None
            cls._entity_extractor_service = None
            cls._cross_encoder_verifier_service = None

    @classmethod
    def get_embedding_service(cls) -> EmbeddingService:
        if cls._embedding_service is None:
            # use_ollama=False frees up the Ollama slot and uses local CPU/GPU via Python
            cls._embedding_service = EmbeddingService(
                use_ollama=False, model_name=config.EMBEDDING_MODEL_NAME
            )
        return cls._embedding_service

    @classmethod
    def get_qdrant_service(cls) -> QdrantService:
        if cls._qdrant_service is None:
            embedding_svc = cls.get_embedding_service()
            cls._qdrant_service = QdrantService(
                collection_name="embeddings_multivector",
                vector_size=embedding_svc.embedding_dimension,
            )
        return cls._qdrant_service

    @classmethod
    async def get_redis_service(cls) -> RedisService:
        if cls._redis_service is None:
            cls._redis_service = RedisService()
        await cls._redis_service.ping()
        return cls._redis_service

    @classmethod
    def get_reranker_service(cls) -> RerankerService:
        if cls._reranker_service is None:
            cls._reranker_service = RerankerService()
        return cls._reranker_service

    @classmethod
    def get_llm_provider_service(cls) -> LlmProviderService:
        if cls._llm_provider_service is None:
            cls._llm_provider_service = LlmProviderService()
        return cls._llm_provider_service

    @classmethod
    async def get_intent_classifier_service(cls) -> IntentClassifierService:
        if cls._intent_classifier_service is None:
            cls._intent_classifier_service = IntentClassifierService(
                llm_provider_service=cls.get_llm_provider_service()
            )
        return cls._intent_classifier_service

    @classmethod
    def get_entity_extractor_service(cls) -> EntityExtractorService:
        if cls._entity_extractor_service is None:
            cls._entity_extractor_service = EntityExtractorService()
        return cls._entity_extractor_service

    @classmethod
    async def get_llm_router_service(cls) -> LlmRouterService:
        if cls._llm_router_service is None:
            cls._llm_router_service = LlmRouterService(
                llm_provider_service=cls.get_llm_provider_service(),
                embedding_service=cls.get_embedding_service(),
                qdrant_service=cls.get_qdrant_service(),
                redis_service=await cls.get_redis_service(),
                reranker_service=cls.get_reranker_service(),
                intent_classifier_service=await cls.get_intent_classifier_service(),
                entity_extractor_service=cls.get_entity_extractor_service(),
                llm_usage_service=cls.get_llm_usage_service(),
                llm_quota_service=cls.get_llm_quota_service(),
            )
        return cls._llm_router_service

    @classmethod
    def get_llm_usage_service(cls) -> LlmUsageService:
        if cls._llm_usage_service is None:
            cls._llm_usage_service = LlmUsageService(
                db_session=next(get_db()), db_session_read=next(get_db_read())
            )
        return cls._llm_usage_service

    @classmethod
    def get_llm_quota_service(cls) -> LlmQuotaService:
        if cls._llm_quota_service is None:
            import asyncio

            redis = (
                asyncio.run(cls.get_redis_service())
                if not cls._redis_service
                else cls._redis_service
            )
            cls._llm_quota_service = LlmQuotaService(
                redis_service=redis, db_session=next(get_db())
            )
        return cls._llm_quota_service

    @classmethod
    def get_cross_encoder_verifier_service(cls) -> CrossEncoderVerifierService:
        if cls._cross_encoder_verifier_service is None:
            cls._cross_encoder_verifier_service = CrossEncoderVerifierService()
        return cls._cross_encoder_verifier_service


# FastAPI Depends compatible functions
def get_embedding_service() -> EmbeddingService:
    return DependencyContainer.get_embedding_service()


def get_qdrant_service() -> QdrantService:
    return DependencyContainer.get_qdrant_service()


async def get_redis_service() -> RedisService:
    return await DependencyContainer.get_redis_service()


def get_reranker_service() -> RerankerService:
    return DependencyContainer.get_reranker_service()


def get_llm_provider_service() -> LlmProviderService:
    return DependencyContainer.get_llm_provider_service()


async def get_intent_classifier_service() -> IntentClassifierService:
    return await DependencyContainer.get_intent_classifier_service()


def get_entity_extractor_service() -> EntityExtractorService:
    return DependencyContainer.get_entity_extractor_service()


async def get_llm_router_service() -> LlmRouterService:
    return await DependencyContainer.get_llm_router_service()


def get_llm_usage_service() -> LlmUsageService:
    return DependencyContainer.get_llm_usage_service()


def get_llm_quota_service() -> LlmQuotaService:
    return DependencyContainer.get_llm_quota_service()


def get_cross_encoder_verifier_service() -> CrossEncoderVerifierService:
    return DependencyContainer.get_cross_encoder_verifier_service()
