import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.core.logger import log_latency
from app.modules.qdrant.qdrant_client_service import QdrantClientService
from app.modules.qdrant.qdrant_schema import QdrantPayload

logger = logging.getLogger(__name__)


class LlmCacheCollectionService:
    """
    Dedicated Qdrant collection service for LLM prompt and response caching.
    Manages the 'embeddings_multivector' collection schema, hybrid dense/sparse search,
    exact hash lookups, and TTL-based vector expirations.
    """

    def __init__(
        self,
        client_service: QdrantClientService | None = None,
        collection_name: str = "embeddings_multivector",
        vector_size: int = 384,
    ) -> None:
        self.client_service: QdrantClientService = (
            client_service if client_service is not None else QdrantClientService()
        )
        self.collection_name: str = collection_name
        self.vector_size: int = vector_size
        self._init_collection()

    @property
    def client(self) -> QdrantClient:
        """Access underlying QdrantClient for backward compatibility."""
        return self.client_service.client

    def _create_collection(self) -> None:
        logger.info(f"Creating LLM Cache Qdrant collection: {self.collection_name}")
        self.client_service.create_collection(
            collection_name=self.collection_name,
            vectors_config={
                "prompt_embedding": models.VectorParams(
                    size=self.vector_size, distance=models.Distance.COSINE, on_disk=True
                ),
                "system_prompt_embedding": models.VectorParams(
                    size=self.vector_size, distance=models.Distance.COSINE, on_disk=True
                ),
                "user_prompt_embedding": models.VectorParams(
                    size=self.vector_size, distance=models.Distance.COSINE, on_disk=True
                ),
            },
            sparse_vectors_config={
                # "prompt_bm25": models.SparseVectorParams(
                #     index=models.SparseIndexParams(on_disk=False)
                # ),
                "system_prompt_bm25": models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=False)
                ),
                "user_prompt_bm25": models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=False)
                ),
            },
            on_disk_payload=True,  # Large text payloads on SSD
            quantization_config=models.ScalarQuantization(
                scalar=models.ScalarQuantizationConfig(
                    type=models.ScalarType.INT8,
                    quantile=0.99,
                    always_ram=True,  # Quantized vectors in RAM
                )
            ),
        )

        # Create payload indexes for faster filtering
        logger.info("Creating payload indexes for LLM cache fields")
        indexes = [
            ("exact_hash", models.PayloadSchemaType.KEYWORD),
            ("prompt_version", models.PayloadSchemaType.KEYWORD),
            ("model", models.PayloadSchemaType.KEYWORD),
            ("embedding_model", models.PayloadSchemaType.KEYWORD),
            ("embedding_version", models.PayloadSchemaType.KEYWORD),
            ("cacheable", models.PayloadSchemaType.BOOL),
            ("cache_key_version", models.PayloadSchemaType.KEYWORD),
            ("tenant_id", models.PayloadSchemaType.KEYWORD),
            ("user_id", models.PayloadSchemaType.KEYWORD),
            ("session_id", models.PayloadSchemaType.KEYWORD),
            ("conversation_id", models.PayloadSchemaType.KEYWORD),
            ("scope", models.PayloadSchemaType.KEYWORD),
            ("expires_at", models.PayloadSchemaType.INTEGER),
            ("response_format_hash", models.PayloadSchemaType.KEYWORD),
            ("stop_hash", models.PayloadSchemaType.KEYWORD),
            ("completion_tokens", models.PayloadSchemaType.INTEGER),
            ("entities", models.PayloadSchemaType.KEYWORD),
            ("intent", models.PayloadSchemaType.KEYWORD),
            ("core_operation", models.PayloadSchemaType.KEYWORD),
            ("core_subject", models.PayloadSchemaType.KEYWORD),
        ]
        for field_name, field_schema in indexes:
            self.client_service.create_payload_index(
                collection_name=self.collection_name,
                field_name=field_name,
                field_schema=field_schema,
            )

    def _init_collection(self) -> None:
        try:
            if not self.client_service.collection_exists(self.collection_name):
                self._create_collection()
            else:
                logger.info(
                    f"Qdrant collection '{self.collection_name}' already exists. Checking vector dimensions."
                )
                collection_info = self.client_service.get_collection(
                    self.collection_name
                )
                vectors_config = collection_info.config.params.vectors
                existing_size = None

                if (
                    isinstance(vectors_config, dict)
                    and "prompt_embedding" in vectors_config
                ):
                    existing_size = vectors_config["prompt_embedding"].size
                elif hasattr(vectors_config, "size"):
                    existing_size = vectors_config.size

                if existing_size is not None and existing_size != self.vector_size:
                    logger.warning(
                        f"Vector dimension mismatch! Existing collection has size {existing_size}, "
                        f"but current config expects {self.vector_size}. Recreating collection."
                    )
                    self.client_service.delete_collection(self.collection_name)
                    self._create_collection()
        except Exception:
            logger.exception("Failed to initialize Qdrant collection")

    def close(self) -> None:
        """Close Qdrant connection."""
        self.client_service.close()

    @log_latency()
    def search_exact(
        self, exact_hash: str, tenant_id: str | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            import time

            now = int(time.time())
            must_conditions = [
                models.FieldCondition(
                    key="exact_hash",
                    match=models.MatchValue(value=exact_hash),
                ),
                models.FieldCondition(
                    key="cacheable",
                    match=models.MatchValue(value=True),
                ),
                models.FieldCondition(
                    key="expires_at",
                    range=models.Range(gte=now),
                ),
            ]
            if tenant_id:
                must_conditions.append(
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=tenant_id),
                    )
                )

            results, _ = self.client_service.scroll(
                collection_name=self.collection_name,
                scroll_filter=models.Filter(must=must_conditions),
                limit=1,
                with_payload=True,
            )
            if results:
                logger.info("Exact search hit in Qdrant!")
                return results[0].payload, str(results[0].id)
            return None, None
        except Exception:
            logger.exception("Failed to search exact in Qdrant")
            return None, None

    @log_latency()
    def query_points_rrf(
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

            results = self.client_service.query_points(
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
        except Exception:
            logger.exception("Failed to search in Qdrant")
            return []

    @log_latency()
    def query_points_dense(
        self,
        vector: list[float] | None = None,
        using: str | None = None,
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

            results = self.client_service.query_points(
                collection_name=self.collection_name,
                query=vector,
                using=using,
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
        except Exception:
            logger.exception("Failed to search dense in Qdrant")
            return []

    @log_latency()
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
        intent: str | None = None,
        core_operation: str | None = None,
        core_subject: str | None = None,
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
                intent=intent,
                core_operation=core_operation,
                core_subject=core_subject,
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

            self.client_service.upsert(
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

    @log_latency()
    def delete_expired(self) -> None:
        """Deletes vectors where expires_at is less than the current time."""
        try:
            import time

            now = int(time.time())

            logger.info(f"Deleting Qdrant points where expires_at < {now}")

            self.client_service.delete(
                collection_name=self.collection_name,
                points_selector=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="expires_at",
                            range=models.Range(lt=now),
                        )
                    ]
                ),
            )
            logger.info("Successfully requested deletion of expired Qdrant points.")
        except Exception:
            logger.exception("Failed to delete expired vectors from Qdrant")
