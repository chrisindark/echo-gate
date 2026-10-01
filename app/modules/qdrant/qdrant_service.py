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
                    vectors_config={
                        "prompt_embedding": models.VectorParams(
                            size=self.vector_size, distance=models.Distance.COSINE
                        ),
                        "system_prompt_embedding": models.VectorParams(
                            size=self.vector_size, distance=models.Distance.COSINE
                        ),
                        "user_prompt_embedding": models.VectorParams(
                            size=self.vector_size, distance=models.Distance.COSINE
                        ),
                        "intent_embedding": models.VectorParams(
                            size=self.vector_size, distance=models.Distance.COSINE
                        ),
                        "response_embedding": models.VectorParams(
                            size=self.vector_size, distance=models.Distance.COSINE
                        ),
                    },
                    sparse_vectors_config={
                        "prompt_bm25": models.SparseVectorParams(
                            index=models.SparseIndexParams(on_disk=False)
                        ),
                        "system_prompt_bm25": models.SparseVectorParams(
                            index=models.SparseIndexParams(on_disk=False)
                        ),
                        "user_prompt_bm25": models.SparseVectorParams(
                            index=models.SparseIndexParams(on_disk=False)
                        ),
                    },
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
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="user_id",
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="session_id",
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="conversation_id",
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="scope",
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="entities",
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="response_format_hash",
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
        system_prompt_vector: list[float] | None = None,
        system_prompt_sparse: dict[str, list] | None = None,
        user_prompt_vector: list[float] | None = None,
        user_prompt_sparse: dict[str, list] | None = None,
        filter_payload: dict[str, Any] | None = None,
        query_filter: models.Filter | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        try:
            if filter_payload and not query_filter:
                must_conditions = []
                for k, v in filter_payload.items():
                    if isinstance(v, dict):
                        # Support range filters like {"gte": 123}
                        must_conditions.append(
                            models.FieldCondition(key=k, range=models.Range(**v))
                        )
                    else:
                        must_conditions.append(
                            models.FieldCondition(
                                key=k, match=models.MatchValue(value=v)
                            )
                        )
                query_filter = models.Filter(must=must_conditions)

            prefetch = []

            if system_prompt_vector:
                prefetch.append(
                    models.Prefetch(
                        query=system_prompt_vector,
                        using="system_prompt_embedding",
                        limit=limit * 2,
                    )
                )
            if system_prompt_sparse and system_prompt_sparse.get("indices"):
                prefetch.append(
                    models.Prefetch(
                        query=models.SparseVector(
                            indices=system_prompt_sparse["indices"],
                            values=system_prompt_sparse["values"],
                        ),
                        using="system_prompt_bm25",
                        limit=limit * 2,
                    )
                )

            if user_prompt_vector:
                prefetch.append(
                    models.Prefetch(
                        query=user_prompt_vector,
                        using="user_prompt_embedding",
                        limit=limit * 2,
                    )
                )
            if user_prompt_sparse and user_prompt_sparse.get("indices"):
                prefetch.append(
                    models.Prefetch(
                        query=models.SparseVector(
                            indices=user_prompt_sparse["indices"],
                            values=user_prompt_sparse["values"],
                        ),
                        using="user_prompt_bm25",
                        limit=limit * 2,
                    )
                )

            if not prefetch:
                return []

            results = self.client.query_points(
                collection_name=self.collection_name,
                prefetch=prefetch,
                query=models.FusionQuery(fusion=models.Fusion.RRF),
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
        prompt: str,
        response: dict[str, Any],
        system_prompt_vector: list[float] | None = None,
        system_prompt_sparse: dict[str, list] | None = None,
        user_prompt_vector: list[float] | None = None,
        user_prompt_sparse: dict[str, list] | None = None,
        prompt_vector: list[float] | None = None,
        prompt_sparse: dict[str, list] | None = None,
        system_prompt: str | None = None,
        user_prompt: str | None = None,
        exact_hash: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        conversation_id: str | None = None,
        model: str | None = None,
        service_name: str | None = None,
        scope: str = "GLOBAL",
        entities: list[str] | None = None,
        time_sensitivity: float | None = None,
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
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response=response,
                exact_hash=exact_hash,
                model=model,
                service_name=service_name,
                scope=scope,
                entities=entities or [],
                time_sensitivity=time_sensitivity,
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                conversation_id=conversation_id,
                embedding_model=embedding_model,
                embedding_version=embedding_version,
                cacheable=cacheable,
                cache_key_version=cache_key_version,
                created_at=created_at,
                expires_at=expires_at,
                metadata=metadata,
            )

            # Construct the point vector using multiple named vectors
            point_vector = {}
            if system_prompt_vector:
                point_vector["system_prompt_embedding"] = system_prompt_vector
            if system_prompt_sparse and system_prompt_sparse.get("indices"):
                point_vector["system_prompt_bm25"] = models.SparseVector(
                    indices=system_prompt_sparse["indices"],
                    values=system_prompt_sparse["values"],
                )
            if user_prompt_vector:
                point_vector["user_prompt_embedding"] = user_prompt_vector
            if user_prompt_sparse and user_prompt_sparse.get("indices"):
                point_vector["user_prompt_bm25"] = models.SparseVector(
                    indices=user_prompt_sparse["indices"],
                    values=user_prompt_sparse["values"],
                )
            if prompt_vector:
                point_vector["prompt_embedding"] = prompt_vector
            if prompt_sparse and prompt_sparse.get("indices"):
                point_vector["prompt_bm25"] = models.SparseVector(
                    indices=prompt_sparse["indices"],
                    values=prompt_sparse["values"],
                )

            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    models.PointStruct(
                        id=point_id,
                        vector=point_vector,
                        payload=payload_obj.to_qdrant_dict(),
                    )
                ],
                wait=False,
            )
            logger.info(f"Asynchronously saved response to Qdrant with ID: {point_id}")
            return point_id
        except Exception:
            logger.exception("Failed to save to Qdrant")
            return None
