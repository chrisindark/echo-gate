import asyncio
import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.core.config import config
from app.core.degradation import mark_cache_degraded
from app.core.logger import log_latency
from app.modules.qdrant.qdrant_client_service import QdrantClientService
from app.modules.qdrant.qdrant_schema import QdrantPayload
from app.modules.qdrant.retention_policy import compute_retention_value

logger = logging.getLogger(__name__)

# Bulk eviction is an operator action behind an admin key, but it can still be
# destructive: bound how many points one request may delete and require the
# caller to narrow the filter rather than sweeping the whole collection.
MAX_BULK_EVICT_POINTS = 10_000


def build_payload_filter(
    filter_payload: dict[str, Any] | None,
) -> models.Filter | None:
    """Translate ``{field: value}`` / ``{field: {"gte": n}}`` into a Qdrant filter."""
    if not filter_payload:
        return None

    must_conditions = []
    for key, value in filter_payload.items():
        if isinstance(value, dict):
            # Support range filters like {"gte": 123}
            must_conditions.append(
                models.FieldCondition(key=key, range=models.Range(**value))
            )
        else:
            must_conditions.append(
                models.FieldCondition(key=key, match=models.MatchValue(value=value))
            )
    return models.Filter(must=must_conditions)


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

    PAYLOAD_INDEXES: list[tuple[str, models.PayloadSchemaType]] = [
        ("exact_hash", models.PayloadSchemaType.KEYWORD),
        ("prompt_version", models.PayloadSchemaType.KEYWORD),
        ("model", models.PayloadSchemaType.KEYWORD),
        ("embedding_model", models.PayloadSchemaType.KEYWORD),
        ("embedding_version", models.PayloadSchemaType.KEYWORD),
        ("sparse_embedding_version", models.PayloadSchemaType.KEYWORD),
        ("intent_prompt_version", models.PayloadSchemaType.KEYWORD),
        ("reranker_version", models.PayloadSchemaType.KEYWORD),
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
        ("subject_modifier", models.PayloadSchemaType.KEYWORD),
        ("action_modifier", models.PayloadSchemaType.KEYWORD),
        ("provider_cost", models.PayloadSchemaType.FLOAT),
        ("latency_ms", models.PayloadSchemaType.INTEGER),
        ("token_count", models.PayloadSchemaType.INTEGER),
        ("retention_value", models.PayloadSchemaType.FLOAT),
        ("hit_count", models.PayloadSchemaType.INTEGER),
    ]

    @property
    def client(self) -> QdrantClient:
        """Access underlying QdrantClient for backward compatibility."""
        return self.client_service.client

    def ensure_indexes(self) -> None:
        """
        Idempotently verify and create all required payload indexes on the collection.
        Inspects existing indexes on the collection and creates any missing ones.
        """
        try:
            if not self.client_service.collection_exists(self.collection_name):
                logger.warning(
                    f"Collection '{self.collection_name}' does not exist. Skipping ensure_indexes."
                )
                return

            collection_info = self.client_service.get_collection(self.collection_name)
            existing_indexes: set[str] = set()
            if (
                hasattr(collection_info, "payload_schema")
                and collection_info.payload_schema
            ):
                existing_indexes = set(collection_info.payload_schema.keys())

            logger.info(
                f"Ensuring payload indexes on '{self.collection_name}' "
                f"({len(existing_indexes)} existing index(es) found)"
            )

            for field_name, field_schema in self.PAYLOAD_INDEXES:
                if field_name not in existing_indexes:
                    try:
                        logger.info(
                            f"Creating payload index: {field_name} ({field_schema}) on {self.collection_name}"
                        )
                        self.client_service.create_payload_index(
                            collection_name=self.collection_name,
                            field_name=field_name,
                            field_schema=field_schema,
                        )
                        existing_indexes.add(field_name)
                    except Exception as e:
                        logger.warning(
                            f"Could not create payload index '{field_name}' on '{self.collection_name}': {e}"
                        )
                else:
                    logger.debug(
                        f"Payload index '{field_name}' already exists on '{self.collection_name}'."
                    )
        except Exception:
            logger.exception(
                f"Failed to ensure payload indexes on Qdrant collection '{self.collection_name}'"
            )

    def _create_collection(self) -> None:
        logger.info(f"Creating LLM Cache Qdrant collection: {self.collection_name}")
        cluster_kwargs: dict[str, Any] = {}
        if config.QDRANT_REPLICATION_FACTOR is not None:
            cluster_kwargs["replication_factor"] = config.QDRANT_REPLICATION_FACTOR
        if config.QDRANT_WRITE_CONSISTENCY_FACTOR is not None:
            cluster_kwargs["write_consistency_factor"] = (
                config.QDRANT_WRITE_CONSISTENCY_FACTOR
            )

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
            **cluster_kwargs,
        )
        self.ensure_indexes()

    def _init_collection(self) -> None:
        try:
            if not self.client_service.collection_exists(self.collection_name):
                self._create_collection()
                return

            logger.info(
                f"Qdrant collection '{self.collection_name}' already exists. Checking vector dimensions."
            )
            collection_info = self.client_service.get_collection(self.collection_name)
            vectors_config = collection_info.config.params.vectors
        except Exception:
            logger.exception("Failed to initialize Qdrant collection")
            return

        existing_size = None
        if isinstance(vectors_config, dict) and "prompt_embedding" in vectors_config:
            existing_size = vectors_config["prompt_embedding"].size
        elif hasattr(vectors_config, "size"):
            existing_size = vectors_config.size

        if existing_size is not None and existing_size != self.vector_size:
            raise ValueError(
                f"Vector dimension mismatch! Existing collection '{self.collection_name}' "
                f"has size {existing_size}, but current config expects {self.vector_size}. "
                "The semantic cache would silently fail; recreate the collection or fix "
                "EMBEDDING_MODEL_NAME."
            )

        try:
            self.ensure_indexes()
        except Exception:
            logger.exception(
                f"Failed to ensure payload indexes on Qdrant collection '{self.collection_name}'"
            )

    def close(self) -> None:
        """Close Qdrant connection."""
        self.client_service.close()

    async def aclose(self) -> None:
        """Close Qdrant connection asynchronously."""
        if hasattr(self.client_service, "aclose"):
            await self.client_service.aclose()
        else:
            await asyncio.to_thread(self.client_service.close)

    async def ensure_indexes_async(self) -> None:
        """Asynchronously ensure payload indexes without blocking the event loop."""
        await asyncio.to_thread(self.ensure_indexes)

    async def search_exact_async(
        self, exact_hash: str, tenant_id: str | None = None
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Asynchronously search for exact hash match in Qdrant."""
        return await asyncio.to_thread(
            self.search_exact, exact_hash, tenant_id=tenant_id
        )

    async def search_by_exact_hash_async(
        self,
        exact_hash: str,
        filter_payload: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Asynchronously search entries by exact hash for the admin inspector."""
        return await asyncio.to_thread(
            self.search_by_exact_hash,
            exact_hash,
            filter_payload=filter_payload,
            limit=limit,
        )

    async def query_points_rrf_async(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Asynchronously query points using hybrid RRF search."""
        return await asyncio.to_thread(self.query_points_rrf, **kwargs)

    async def query_points_dense_async(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Asynchronously query points using dense vector search."""
        return await asyncio.to_thread(self.query_points_dense, **kwargs)

    async def upsert_async(self, **kwargs: Any) -> str | None:
        """Asynchronously upsert a point into Qdrant."""
        return await asyncio.to_thread(self.upsert, **kwargs)

    async def delete_point_async(self, point_id: str) -> bool:
        """Asynchronously delete a point by ID."""
        return await asyncio.to_thread(self.delete_point, point_id)

    async def record_hit_async(self, point_id: str) -> bool:
        """Asynchronously record a cache hit and bump retention score."""
        return await asyncio.to_thread(self.record_hit, point_id)

    async def bulk_evict_async(self, **kwargs: Any) -> dict[str, Any]:
        """Asynchronously perform bulk cache eviction."""
        return await asyncio.to_thread(self.bulk_evict, **kwargs)

    async def delete_expired_async(self) -> None:
        """Asynchronously delete expired vectors."""
        await asyncio.to_thread(self.delete_expired)

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
            mark_cache_degraded("qdrant_exact_search_failed")
            return None, None

    def search_by_exact_hash(
        self,
        exact_hash: str,
        filter_payload: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Look up entries by exact hash for the admin inspector.

        Unlike :meth:`search_exact` this applies **no** ``cacheable`` or
        ``expires_at`` condition -- an inspector has to be able to see the
        non-cacheable and lapsed rows too -- and it raises on a Qdrant failure
        instead of degrading to ``None``, because an empty result would read as
        a clean miss rather than an outage.
        """
        conditions: dict[str, Any] = {"exact_hash": exact_hash}
        conditions.update(filter_payload or {})

        records, _ = self.client_service.scroll(
            collection_name=self.collection_name,
            scroll_filter=build_payload_filter(conditions),
            limit=limit,
            with_payload=True,
        )
        return [
            {"payload": record.payload or {}, "score": None, "id": str(record.id)}
            for record in records
        ]

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
        prefetch_score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid dense+sparse RRF search.

        ``score_threshold`` filters the fused result set, whose scores are rank
        fusion scores rather than cosine similarities. ``prefetch_score_threshold``
        instead floors each **dense** prefetch branch by cosine similarity (the
        sparse BM25 branch is left alone: BM25 scores live on a different scale).
        """

        try:
            if filter_payload and not query_filter:
                query_filter = build_payload_filter(filter_payload)

            prefetch = []

            if system_prompt_vector:
                prefetch.append(
                    models.Prefetch(
                        query=system_prompt_vector,
                        using="system_prompt_embedding",
                        limit=limit * 2,
                        score_threshold=prefetch_score_threshold,
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
                        score_threshold=prefetch_score_threshold,
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
            mark_cache_degraded("qdrant_hybrid_search_failed")
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
                query_filter = build_payload_filter(filter_payload)

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
            mark_cache_degraded("qdrant_dense_search_failed")
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
        provider_cost: float | None = None,
        latency_ms: int | None = None,
        token_count: int | None = None,
        retention_value: float | None = None,
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
                provider_cost=provider_cost,
                latency_ms=latency_ms,
                token_count=token_count,
                retention_value=retention_value,
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
            # if prompt_sparse and prompt_sparse.get("indices"):
            #     point_vector["prompt_bm25"] = models.SparseVector(
            #         indices=prompt_sparse["indices"],
            #         values=prompt_sparse["values"],
            #     )

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
    def delete_point(self, point_id: str) -> bool:
        """Deletes a single vector from Qdrant by point ID (instant eviction kill switch)."""
        try:
            logger.info(
                "Evicting point %s from Qdrant collection %s",
                point_id,
                self.collection_name,
            )
            self.client_service.delete(
                collection_name=self.collection_name,
                points_selector=models.PointIdsList(points=[point_id]),
                wait=True,
            )
            logger.info("Successfully evicted point %s from Qdrant", point_id)
            return True
        except Exception:
            logger.exception("Failed to delete point %s from Qdrant", point_id)
            return False

    def record_hit(self, point_id: str) -> bool:
        """Bump ``hit_count`` and refresh ``retention_value`` for a served entry.

        Qdrant has no atomic counter, so this is a read-modify-write. Callers run
        it as a background task: a lost increment only costs an eviction
        priority nudge, never a response.
        """
        try:
            records = self.client_service.retrieve(
                collection_name=self.collection_name,
                ids=[point_id],
                with_payload=True,
            )
            if not records:
                logger.debug(
                    "Cache hit on unknown point %s; skipping increment", point_id
                )
                return False

            payload = records[0].payload or {}
            hit_count = int(payload.get("hit_count") or 0) + 1
            retention_value = compute_retention_value(
                payload.get("provider_cost"),
                payload.get("latency_ms"),
                payload.get("token_count"),
                hit_count,
            )
            self.client_service.set_payload(
                collection_name=self.collection_name,
                payload={"hit_count": hit_count, "retention_value": retention_value},
                points=[point_id],
            )
            logger.debug(
                "Recorded cache hit %s for point %s (retention_value=%.3f)",
                hit_count,
                point_id,
                retention_value,
            )
            return True
        except Exception:
            logger.warning("Failed to record cache hit for point %s", point_id)
            return False

    def _delete_points_matching(self, points_filter: models.Filter) -> None:
        self.client_service.delete(
            collection_name=self.collection_name,
            points_selector=points_filter,
        )

    def bulk_evict(
        self,
        *,
        intent: str | None = None,
        model: str | None = None,
        scope: str | None = None,
        tenant_id: str | None = None,
        older_than_days: float | None = None,
        expired_only: bool = False,
        limit: int = MAX_BULK_EVICT_POINTS,
        dry_run: bool = True,
        now: int | None = None,
    ) -> dict[str, Any]:
        """Evict every cache entry matching an operator filter.

        Returns ``{"matched", "deleted", "point_ids", "exact_hashes"}``.
        ``matched`` is the exact filter count taken before anything is removed,
        so a dry run and a real sweep report the same number. ``exact_hashes``
        lets the caller purge the Redis copies too: deleting only the vectors
        would leave exact hits servable.

        Age filters compare ``created_at``, which entries written before that
        field existed do not carry -- those rows only match the field filters.

        Raises ``ValueError`` for an empty filter (a mistake, not a sweep of the
        whole collection) and when the filter matches more than ``limit``
        points, so an operator has to narrow it rather than issue an unbounded
        delete.
        """
        import time

        if now is None:
            now = int(time.time())

        must: list[models.FieldCondition] = []
        for key, value in (
            ("intent", intent),
            ("model", model),
            ("scope", scope),
            ("tenant_id", tenant_id),
        ):
            if value:
                must.append(
                    models.FieldCondition(key=key, match=models.MatchValue(value=value))
                )

        if older_than_days is not None:
            if older_than_days <= 0:
                raise ValueError("older_than_days must be positive")
            must.append(
                models.FieldCondition(
                    key="created_at",
                    range=models.Range(lt=int(now - older_than_days * 86400)),
                )
            )

        if expired_only:
            must.append(
                models.FieldCondition(key="expires_at", range=models.Range(lt=now))
            )

        if not must:
            raise ValueError("bulk eviction requires at least one filter")

        eviction_filter = models.Filter(must=must)
        matched = int(
            self.client_service.count(
                collection_name=self.collection_name,
                count_filter=eviction_filter,
                exact=True,
            )
        )

        if dry_run or matched == 0:
            return {
                "matched": matched,
                "deleted": 0,
                "point_ids": [],
                "exact_hashes": [],
            }

        if matched > limit:
            raise ValueError(
                f"filter matches {matched} entries, above the {limit} limit; "
                "narrow the filter or raise the limit"
            )

        # Only the exact hash is needed for the Redis purge; asking for the
        # whole payload would pull every cached completion into memory.
        records, _ = self.client_service.scroll(
            collection_name=self.collection_name,
            scroll_filter=eviction_filter,
            limit=matched,
            with_payload=["exact_hash"],
        )
        point_ids = [str(record.id) for record in records]
        exact_hashes = list(
            {
                (record.payload or {}).get("exact_hash")
                for record in records
                if (record.payload or {}).get("exact_hash")
            }
        )

        logger.info(
            "Bulk eviction from '%s': %s point(s) matching %s",
            self.collection_name,
            matched,
            {
                "intent": intent,
                "model": model,
                "scope": scope,
                "tenant_id": tenant_id,
                "older_than_days": older_than_days,
                "expired_only": expired_only,
            },
        )
        self.client_service.delete(
            collection_name=self.collection_name,
            points_selector=eviction_filter,
            wait=True,
        )
        return {
            "matched": matched,
            "deleted": matched,
            "point_ids": point_ids,
            "exact_hashes": exact_hashes,
        }

    def evict_expired(self) -> None:
        """Evict lapsed entries, protecting the valuable ones from a flat sweep.

        Cheap entries (low provider cost, short latency, few hits) are deleted as
        soon as their TTL lapses. Entries at or above
        ``CACHE_RETENTION_PROTECT_THRESHOLD`` get
        ``CACHE_RETENTION_GRACE_PERIOD_SECONDS`` of extra life, so a re-warm or a
        TTL bump does not have to pay for re-embedding and re-generating them.
        Points written before retention metadata existed have no score and are
        treated as cheap.
        """
        import time

        now = int(time.time())

        if not config.CACHE_RETENTION_ENABLED:
            logger.info(f"Deleting Qdrant points where expires_at < {now}")
            self._delete_points_matching(
                models.Filter(
                    must=[
                        models.FieldCondition(
                            key="expires_at",
                            range=models.Range(lt=now),
                        )
                    ]
                )
            )
            return

        threshold = config.CACHE_RETENTION_PROTECT_THRESHOLD
        logger.info(
            "Evicting expired Qdrant points with retention_value < %s (expires_at < %s)",
            threshold,
            now,
        )
        self._delete_points_matching(
            models.Filter(
                must=[
                    models.FieldCondition(
                        key="expires_at",
                        range=models.Range(lt=now),
                    )
                ],
                should=[
                    models.FieldCondition(
                        key="retention_value",
                        range=models.Range(lt=threshold),
                    ),
                    models.IsEmptyCondition(
                        is_empty=models.PayloadField(key="retention_value")
                    ),
                ],
            )
        )

        grace_cutoff = now - config.CACHE_RETENTION_GRACE_PERIOD_SECONDS
        logger.info(
            "Evicting protected Qdrant points past the grace period (expires_at < %s)",
            grace_cutoff,
        )
        self._delete_points_matching(
            models.Filter(
                must=[
                    models.FieldCondition(
                        key="expires_at",
                        range=models.Range(lt=grace_cutoff),
                    )
                ]
            )
        )

    def evict_over_capacity(self) -> int:
        """Delete the lowest-value points when the collection exceeds its cap.

        No-op unless ``CACHE_RETENTION_MAX_POINTS`` is positive. Returns the
        number of points evicted.
        """
        max_points = config.CACHE_RETENTION_MAX_POINTS
        if max_points <= 0:
            return 0

        total = self.client_service.count(
            collection_name=self.collection_name, exact=False
        )
        overflow = total - max_points
        if overflow <= 0:
            return 0

        # Bound the work per sweep: the task runs on the eviction schedule, so
        # a large overage drains across runs instead of in one long delete.
        batch_size = min(overflow, 1000)
        records, _ = self.client_service.scroll(
            collection_name=self.collection_name,
            limit=batch_size,
            with_payload=False,
            order_by=models.OrderBy(
                key="retention_value", direction=models.Direction.ASC
            ),
        )
        point_ids = [str(record.id) for record in records]
        if not point_ids:
            return 0

        logger.info(
            "Evicting %s low-value point(s) from '%s' to enforce max_points=%s",
            len(point_ids),
            self.collection_name,
            max_points,
        )
        self.client_service.delete(
            collection_name=self.collection_name,
            points_selector=models.PointIdsList(points=point_ids),
        )
        return len(point_ids)

    @log_latency()
    def delete_expired(self) -> None:
        """Run the value-aware eviction sweep (TTL then capacity)."""
        try:
            self.evict_expired()
            self.evict_over_capacity()
            logger.info("Successfully requested deletion of expired Qdrant points.")
        except Exception:
            logger.exception("Failed to delete expired vectors from Qdrant")
