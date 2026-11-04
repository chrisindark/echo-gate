import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import (
    get_embedding_service,
    get_llm_cache_collection_service,
)
from app.core.guards import require_admin_scope
from app.core.responses import BaseAPIResponse
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.embedding.embedding_service import EmbeddingService
from app.modules.inference.inference_client import InferenceUnavailable
from app.modules.qdrant.llm_cache_collection_service import LlmCacheCollectionService
from app.modules.qdrant.qdrant_schema import QdrantSearchRequest, QdrantSearchResponse

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/qdrant", tags=["Qdrant"])

# These return raw cache payloads -- prompts and completions -- for every tenant
# at once, with no tenant filter available in the request. They are operator
# tooling, so they require an admin key with ``cache:read`` rather than being
# reachable anonymously or with a tenant ``X-API-Key``.
RequireCacheRead = require_admin_scope("cache:read")


@api_v1_router.post("/search", response_model=BaseAPIResponse[QdrantSearchResponse])
async def search_qdrant(
    request: QdrantSearchRequest,
    identity: AdminKeyIdentity = Depends(RequireCacheRead),
    llm_cache_service: LlmCacheCollectionService = Depends(
        get_llm_cache_collection_service
    ),
    embedder: EmbeddingService = Depends(get_embedding_service),
) -> BaseAPIResponse[QdrantSearchResponse]:
    """
    Search the Qdrant service using semantic similarity.
    """
    try:
        system_prompt_vector = None
        user_prompt_vector = None

        system_prompt = next(
            (m.content for m in request.messages if m.role == "system"), None
        )
        user_prompt = next(
            (m.content for m in request.messages if m.role == "user"), None
        )

        if system_prompt:
            system_prompt_vector = await embedder.get_embedding_async(system_prompt)

        if user_prompt:
            user_prompt_vector = await embedder.get_embedding_async(user_prompt)

        # Prepare filter if provided
        filter_payload = None
        if request.model_filter:
            filter_payload = {"model": request.model_filter}

        # Search Qdrant
        if user_prompt_vector:
            result_payload = await asyncio.to_thread(
                llm_cache_service.query_points_dense,
                vector=user_prompt_vector,
                using="user_prompt_embedding",
                filter_payload=filter_payload,
                score_threshold=request.threshold,
            )
        elif system_prompt_vector:
            result_payload = await asyncio.to_thread(
                llm_cache_service.query_points_dense,
                vector=system_prompt_vector,
                using="system_prompt_embedding",
                filter_payload=filter_payload,
                score_threshold=request.threshold,
            )
        else:
            result_payload = []

        logger.info(
            f"Qdrant diagnostic search via admin key {identity.key_id}: "
            f"{len(result_payload)} matches"
        )
        if result_payload:
            return BaseAPIResponse.success_response(
                QdrantSearchResponse(found=True, payload=result_payload)
            )
        else:
            return BaseAPIResponse.success_response(QdrantSearchResponse(found=False))

    except InferenceUnavailable as e:
        # An operator endpoint, unlike the serving path: report the engine being
        # down accurately instead of dressing it up as a Qdrant failure.
        logger.warning(f"Inference engine unavailable during Qdrant search: {e}")
        raise HTTPException(
            status_code=503, detail=f"Inference engine unavailable: {e}"
        )
    except Exception as e:
        logger.error(f"Error during Qdrant search: {e}")
        raise HTTPException(status_code=500, detail="Error searching Qdrant")


@api_v1_router.post("/search/rrf", response_model=BaseAPIResponse[QdrantSearchResponse])
async def search_qdrant_rrf(
    request: QdrantSearchRequest,
    identity: AdminKeyIdentity = Depends(RequireCacheRead),
    llm_cache_service: LlmCacheCollectionService = Depends(
        get_llm_cache_collection_service
    ),
    embedder: EmbeddingService = Depends(get_embedding_service),
) -> BaseAPIResponse[QdrantSearchResponse]:
    """
    Search the Qdrant service using semantic similarity with RRF.
    """
    try:
        system_prompt_vector = None
        system_prompt_sparse = None
        user_prompt_vector = None
        user_prompt_sparse = None

        system_prompt = next(
            (m.content for m in request.messages if m.role == "system"), None
        )
        user_prompt = next(
            (m.content for m in request.messages if m.role == "user"), None
        )

        if system_prompt:
            system_prompt_vector = await embedder.get_embedding_async(system_prompt)
            system_prompt_sparse = await asyncio.to_thread(
                embedder.get_sparse_embedding, system_prompt
            )

        if user_prompt:
            user_prompt_vector = await embedder.get_embedding_async(user_prompt)
            user_prompt_sparse = await asyncio.to_thread(
                embedder.get_sparse_embedding, user_prompt
            )

        # Prepare filter if provided
        filter_payload = None
        if request.model_filter:
            filter_payload = {"model": request.model_filter}

        # Search Qdrant
        result_payload = await asyncio.to_thread(
            llm_cache_service.query_points_rrf,
            system_prompt_vector=system_prompt_vector,
            system_prompt_sparse=system_prompt_sparse,
            user_prompt_vector=user_prompt_vector,
            user_prompt_sparse=user_prompt_sparse,
            filter_payload=filter_payload,
            prefetch_score_threshold=request.threshold,
        )

        logger.info(
            f"Qdrant RRF search via admin key {identity.key_id}: "
            f"{len(result_payload)} matches"
        )
        if result_payload:
            return BaseAPIResponse.success_response(
                QdrantSearchResponse(found=True, payload=result_payload)
            )
        else:
            return BaseAPIResponse.success_response(QdrantSearchResponse(found=False))

    except InferenceUnavailable as e:
        logger.warning(f"Inference engine unavailable during Qdrant RRF search: {e}")
        raise HTTPException(
            status_code=503, detail=f"Inference engine unavailable: {e}"
        )
    except Exception as e:
        logger.error(f"Error during Qdrant search RRF: {e}")
        raise HTTPException(status_code=500, detail="Error searching Qdrant with RRF")
