import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_embedding_service, get_qdrant_service
from app.core.responses import BaseAPIResponse
from app.modules.embedding.embedding_service import EmbeddingService
from app.modules.qdrant.qdrant_schema import QdrantSearchRequest, QdrantSearchResponse
from app.modules.qdrant.qdrant_service import QdrantService

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/qdrant", tags=["Qdrant"])


@api_v1_router.post("/search", response_model=BaseAPIResponse[QdrantSearchResponse])
async def search_qdrant(
    request: QdrantSearchRequest,
    qdrantService: QdrantService = Depends(get_qdrant_service),
    embedder: EmbeddingService = Depends(get_embedding_service),
) -> QdrantSearchResponse:
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
            result_payload = qdrantService.query_points_dense(
                vector=user_prompt_vector,
                using="user_prompt_embedding",
                filter_payload=filter_payload,
            )
        elif system_prompt_vector:
            result_payload = qdrantService.query_points_dense(
                vector=system_prompt_vector,
                using="system_prompt_embedding",
                filter_payload=filter_payload,
            )
        else:
            result_payload = []

        if result_payload:
            return BaseAPIResponse.success_response(
                QdrantSearchResponse(found=True, payload=result_payload)
            )
        else:
            return BaseAPIResponse.success_response(QdrantSearchResponse(found=False))

    except Exception as e:
        logger.error(f"Error during Qdrant search: {e}")
        raise HTTPException(status_code=500, detail="Error searching Qdrant")


@api_v1_router.post("/search/rrf", response_model=BaseAPIResponse[QdrantSearchResponse])
async def search_qdrant_rrf(
    request: QdrantSearchRequest,
    qdrantService: QdrantService = Depends(get_qdrant_service),
    embedder: EmbeddingService = Depends(get_embedding_service),
) -> QdrantSearchResponse:
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
            system_prompt_sparse = embedder.get_sparse_embedding(system_prompt)

        if user_prompt:
            user_prompt_vector = await embedder.get_embedding_async(user_prompt)
            user_prompt_sparse = embedder.get_sparse_embedding(user_prompt)

        # Prepare filter if provided
        filter_payload = None
        if request.model_filter:
            filter_payload = {"model": request.model_filter}

        # Search Qdrant
        result_payload = qdrantService.query_points_rrf(
            system_prompt_vector=system_prompt_vector,
            system_prompt_sparse=system_prompt_sparse,
            user_prompt_vector=user_prompt_vector,
            user_prompt_sparse=user_prompt_sparse,
            filter_payload=filter_payload,
        )

        if result_payload:
            return BaseAPIResponse.success_response(
                QdrantSearchResponse(found=True, payload=result_payload)
            )
        else:
            return BaseAPIResponse.success_response(QdrantSearchResponse(found=False))

    except Exception as e:
        logger.error(f"Error during Qdrant search RRF: {e}")
        raise HTTPException(status_code=500, detail="Error searching Qdrant with RRF")
