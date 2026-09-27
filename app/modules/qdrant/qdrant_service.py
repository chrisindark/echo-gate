import logging
import os
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.modules.qdrant.qdrant_schema import QdrantPayload

logger = logging.getLogger(__name__)


class QdrantService:
    def __init__(
        self, collection_name: str = "collection", vector_size: int = 384
    ) -> None:
        qdrant_url: str = os.getenv("QDRANT_URL", "http://localhost:6333")
        logger.info(f"Connecting to Qdrant at {qdrant_url}")
        self.client: QdrantClient = QdrantClient(url=qdrant_url)
        self.collection_name: str = collection_name
        self.vector_size: int = vector_size
        self._init_collection()

    def _init_collection(self) -> None:
        try:
            collections_response = self.client.get_collections()
            collection_names: list[str] = [
                c.name for c in collections_response.collections
            ]

            if self.collection_name not in collection_names:
                logger.info(f"Creating Qdrant collection: {self.collection_name}")
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=self.vector_size, distance=models.Distance.COSINE
                    ),
                )

                # Create payload indexes for faster filtering
                logger.info("Creating payload indexes for 'exact_hash' and 'model'")
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="exact_hash",
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="model",
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="tenant_id",
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            else:
                logger.info(
                    f"Qdrant collection '{self.collection_name}' already exists."
                )
        except Exception as e:
            logger.error(f"Failed to initialize Qdrant collection: {e}")

    def close(self):
        """Close Qdrant connection."""
        self.client.close()

    def search_exact(
        self, exact_hash: str, tenant_id: str | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            must_conditions = [
                models.FieldCondition(
                    key="exact_hash",
                    match=models.MatchValue(value=exact_hash),
                )
            ]
            if tenant_id:
                must_conditions.append(
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=tenant_id),
                    )
                )

            results, _ = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=models.Filter(must=must_conditions),
                limit=1,
                with_payload=True,
            )
            if results:
                logger.info("Exact search hit in Qdrant!")
                return results[0].payload, str(results[0].id)
            return None, None
        except Exception as e:
            logger.error(f"Failed to search exact in Qdrant: {e}")
            return None, None

    def query_points(
        self,
        vector: list[float],
        filter_payload: dict[str, Any] | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
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
                limit=limit,
                with_payload=True,
                score_threshold=score_threshold,
            )

            matches = []
            if results and results.points:
                for point in results.points:
                    matches.append(
                        {
                            "payload": point.payload,
                            "score": point.score,
                            "id": str(point.id),
                        }
                    )
            return matches
        except Exception as e:
            logger.error(f"Failed to search in Qdrant: {e}")
            return []

    def upsert(
        self,
        vector: list[float],
        prompt: str,
        response: dict[str, Any],
        exact_hash: str | None = None,
        tenant_id: str = "anonymous",
        model: str | None = None,
        embedding_model: str | None = None,
        embedding_version: str | None = None,
        cacheable: bool = False,
        cache_key_version: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: int | None = None,
        expires_at: int | None = None,
    ) -> str | None:
        try:
            point_id = str(uuid.uuid4())
            payload_obj = QdrantPayload(
                prompt=prompt,
                response=response,
                exact_hash=exact_hash,
                model=model,
                tenant_id=tenant_id,
                embedding_model=embedding_model,
                embedding_version=embedding_version,
                cacheable=cacheable,
                cache_key_version=cache_key_version,
                created_at=created_at,
                expires_at=expires_at,
                metadata=metadata,
            )

            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    models.PointStruct(
                        id=point_id, vector=vector, payload=payload_obj.to_qdrant_dict()
                    )
                ],
                wait=False,
            )
            logger.info(f"Asynchronously saved response to Qdrant with ID: {point_id}")
            return point_id
        except Exception:
            logger.exception("Failed to save to Qdrant")
            return None
