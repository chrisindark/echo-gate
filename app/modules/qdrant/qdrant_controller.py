import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException

from app.modules.qdrant.qdrant_service import QdrantService
from app.modules.embedding.embedding_service import EmbeddingService
from app.core.dependencies import get_qdrant_service, get_embedding_service

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/qdrant", tags=["Qdrant"])

class QdrantSearchRequest(BaseModel):
    query_text: str
    threshold: float = 0.90
    model_filter: Optional[str] = None

class QdrantSearchResponse(BaseModel):
    found: bool
    payload: Optional[Dict[str, Any]] = None

@api_v1_router.post("/search", response_model=QdrantSearchResponse)
async def search_qdrant(
    request: QdrantSearchRequest,
    qdrant: QdrantService = Depends(get_qdrant_service),
    embedder: EmbeddingService = Depends(get_embedding_service)
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
        result_payload = qdrant.search(
            vector=vector, 
            threshold=request.threshold, 
            filter_payload=filter_payload
        )
        
        if result_payload:
            return QdrantSearchResponse(found=True, payload=result_payload)
        else:
            return QdrantSearchResponse(found=False)
            
    except Exception as e:
        logger.error(f"Error during Qdrant search: {e}")
        raise HTTPException(status_code=500, detail="Error searching Qdrant")
