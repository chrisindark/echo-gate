import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_embedding_service, get_qdrant_service
from app.modules.embedding.embedding_service import EmbeddingService
from app.modules.qdrant.qdrant_schema import (QdrantSearchRequest,
                                              QdrantSearchResponse)
from app.modules.qdrant.qdrant_service import QdrantService

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/qdrant", tags=["Qdrant"])


@api_v1_router.post("/search", response_model=QdrantSearchResponse)
async def search_qdrant(
    request: QdrantSearchRequest,
    qdrantService: QdrantService = Depends(get_qdrant_service),
    embedder: EmbeddingService = Depends(get_embedding_service),
) -> QdrantSearchResponse:
    """
    Search the Qdrant service using semantic similarity.
    """
    try:
        # Get the embedding for the query text
        vector = await embedder.get_embedding_async(request.query_text)

        # Prepare filter if provided
        filter_payload = None
        if request.model_filter:
            filter_payload = {"model": request.model_filter}

        # Search Qdrant
        result_payload = qdrantService.query_points(
            vector=vector, filter_payload=filter_payload
        )

        if result_payload:
            return QdrantSearchResponse(found=True, payload=result_payload)
        else:
            return QdrantSearchResponse(found=False)

    except Exception as e:
        logger.error(f"Error during Qdrant search: {e}")
        raise HTTPException(status_code=500, detail="Error searching Qdrant")
