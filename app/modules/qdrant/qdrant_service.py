import os
import uuid
import logging
from typing import List, Optional, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.modules.qdrant.qdrant_schema import QdrantPayload

logger = logging.getLogger(__name__)

class QdrantService:
    def __init__(self, collection_name: str = "llm_cache", vector_size: int = 384) -> None:
        qdrant_url: str = os.getenv("QDRANT_URL", "http://localhost:6333")
        logger.info(f"Connecting to Qdrant at {qdrant_url}")
        self.client: QdrantClient = QdrantClient(url=qdrant_url)
        self.collection_name: str = collection_name
        self.vector_size: int = vector_size
        self._init_llm_cache_collection()

    def _init_llm_cache_collection(self) -> None:
        try:
            collections_response = self.client.get_collections()
            collection_names: List[str] = [c.name for c in collections_response.collections]

            if self.collection_name not in collection_names:
                logger.info(f"Creating Qdrant collection: {self.collection_name}")
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=self.vector_size,
                        distance=models.Distance.COSINE
                    )
                )

                # Create payload indexes for faster filtering
                logger.info("Creating payload indexes for 'exact_hash' and 'model'")
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="exact_hash",
                    field_schema=models.PayloadSchemaType.KEYWORD
                )
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="model",
                    field_schema=models.PayloadSchemaType.KEYWORD
                )
            else:
                logger.info(f"Qdrant collection '{self.collection_name}' already exists.")
        except Exception as e:
            logger.error(f"Failed to initialize Qdrant collection: {e}")

    def close(self):
        """Close Qdrant connection."""
        self.client.close()

    def search_exact(self, exact_hash: str) -> Optional[Dict[str, Any]]:
        try:
            results, _ = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="exact_hash",
                            match=models.MatchValue(value=exact_hash),
                        )
                    ]
                ),
                limit=1,
                with_payload=True
            )
            if results:
                logger.info("Exact cache hit!")
                return results[0].payload
            return None
        except Exception as e:
            logger.error(f"Failed to search exact in Qdrant: {e}")
            return None

    def query_points(self, vector: List[float], threshold: float = 0.95, filter_payload: Optional[Dict[str, Any]] = None) -> (Optional[Dict[str, Any]], float):
        try:
            query_filter = None
            if filter_payload:
                must_conditions = [
                    models.FieldCondition(key=k, match=models.MatchValue(value=v))
                    for k, v in filter_payload.items()
                ]
                query_filter = models.Filter(must=must_conditions)

            results = self.client.query_points(
                collection_name=self.collection_name,
                query=vector,
                query_filter=query_filter,
                limit=1,
                with_payload=True,
                score_threshold=threshold,
            )
            if results and results.points:
                best_match = results.points[0]
                logger.info(f"Semantic cache hit! Score: {best_match.score:.4f}")
                return best_match.payload, best_match.score
            return None
        except Exception as e:
            logger.error(f"Failed to search in Qdrant: {e}")
            return None

    def upsert(self, vector: List[float], prompt: str, response: Dict[str, Any], exact_hash: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        try:
            point_id = str(uuid.uuid4())

            model = metadata.get("model") if metadata else None
            payload_obj = QdrantPayload(
                prompt=prompt,
                response=response,
                exact_hash=exact_hash,
                model=model,
                metadata=metadata
            )

            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    models.PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload_obj.to_qdrant_dict()
                    )
                ],
                wait=False
            )
            logger.info(f"Asynchronously saved response to Qdrant with ID: {point_id}")
        except Exception as e:
            logger.error(f"Failed to save to Qdrant: {e}")
