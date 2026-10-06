import logging
import os
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.core.config import config

logger = logging.getLogger(__name__)


class QdrantClientService:
    """
    Generic Qdrant client service providing collection-agnostic vector database operations.
    Encapsulates connection management, collection lifecycle, and low-level CRUD operations.
    """

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        client: QdrantClient | None = None,
    ) -> None:
        if client is not None:
            self.client = client
        else:
            qdrant_url = (
                url
                or getattr(config, "QDRANT_URL", None)
                or os.getenv("QDRANT_URL", "http://localhost:6333")
            )
            logger.info(f"Connecting to Qdrant at {qdrant_url}")
            self.client = QdrantClient(url=qdrant_url, api_key=api_key)

    def close(self) -> None:
        """Close Qdrant client connection."""
        try:
            self.client.close()
        except Exception:
            logger.exception("Failed to close Qdrant client connection")

    def get_collections(self) -> list[str]:
        """List all collection names in Qdrant."""
        collections_response = self.client.get_collections()
        return [c.name for c in collections_response.collections]

    def collection_exists(self, collection_name: str) -> bool:
        """Check if a collection exists."""
        return bool(self.client.collection_exists(collection_name))

    def get_collection(self, collection_name: str) -> models.CollectionInfo:
        """Retrieve collection metadata and configuration."""
        return self.client.get_collection(collection_name)

    def create_collection(
        self,
        collection_name: str,
        vectors_config: Any,
        sparse_vectors_config: Any | None = None,
        on_disk_payload: bool = True,
        quantization_config: Any | None = None,
        **kwargs: Any,
    ) -> bool:
        """Create a collection with given vector and quantization configurations."""
        logger.info(f"Creating Qdrant collection: {collection_name}")
        return self.client.create_collection(
            collection_name=collection_name,
            vectors_config=vectors_config,
            sparse_vectors_config=sparse_vectors_config,
            on_disk_payload=on_disk_payload,
            quantization_config=quantization_config,
            **kwargs,
        )

    def delete_collection(self, collection_name: str, **kwargs: Any) -> bool:
        """Delete an existing collection."""
        logger.warning(f"Deleting Qdrant collection: {collection_name}")
        return self.client.delete_collection(collection_name=collection_name, **kwargs)

    def create_payload_index(
        self,
        collection_name: str,
        field_name: str,
        field_schema: models.PayloadSchemaType,
        **kwargs: Any,
    ) -> Any:
        """Create an index on a payload field for fast filtering."""
        return self.client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=field_schema,
            **kwargs,
        )

    def scroll(
        self,
        collection_name: str,
        scroll_filter: models.Filter | None = None,
        limit: int = 1,
        with_payload: bool = True,
        with_vectors: bool = False,
        **kwargs: Any,
    ) -> tuple[list[models.Record], Any]:
        """Scroll points from collection matching filter criteria."""
        return self.client.scroll(
            collection_name=collection_name,
            scroll_filter=scroll_filter,
            limit=limit,
            with_payload=with_payload,
            with_vectors=with_vectors,
            **kwargs,
        )

    def query_points(
        self,
        collection_name: str,
        query: Any = None,
        using: str | None = None,
        prefetch: list[models.Prefetch] | None = None,
        query_filter: models.Filter | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
        with_payload: bool = True,
        with_vectors: bool = False,
        **kwargs: Any,
    ) -> models.QueryResponse:
        """Query points using dense vector, sparse vector, or fusion query."""
        return self.client.query_points(
            collection_name=collection_name,
            query=query,
            using=using,
            prefetch=prefetch,
            query_filter=query_filter,
            limit=limit,
            score_threshold=score_threshold,
            with_payload=with_payload,
            with_vectors=with_vectors,
            **kwargs,
        )

    def upsert(
        self,
        collection_name: str,
        points: list[models.PointStruct],
        wait: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Upsert points into a collection."""
        return self.client.upsert(
            collection_name=collection_name,
            points=points,
            wait=wait,
            **kwargs,
        )

    def delete(
        self,
        collection_name: str,
        points_selector: models.Filter | list[str | int] | models.PointIdsList,
        wait: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Delete points from a collection using filter or ID list."""
        return self.client.delete(
            collection_name=collection_name,
            points_selector=points_selector,
            wait=wait,
            **kwargs,
        )
