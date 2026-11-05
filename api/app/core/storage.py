"""Storage/broker construction seam.

The cloud needs managed-service connection parameters (TLS, IAM auth,
retry policy) for Redis and Qdrant; self-hosters use plain URLs. This
module centralizes both constructors behind ``StorageFactory`` so a cloud
overlay can inject managed-service clients without forking call sites.

OSS defaults preserve current behavior exactly: ``RedisService()`` from
``config.REDIS_URL`` and ``QdrantClientService()`` from ``config.QDRANT_URL``.
"""

from __future__ import annotations

from app.core.config import config
from app.modules.qdrant.qdrant_client_service import QdrantClientService
from app.modules.redis.redis_service import RedisService


class StorageFactory:
    """Builds Redis and Qdrant clients from config (OSS defaults)."""

    @staticmethod
    def redis(url: str | None = None) -> RedisService:
        return RedisService(redis_url=url or config.REDIS_URL)

    @staticmethod
    def qdrant(
        url: str | None = None, api_key: str | None = None
    ) -> QdrantClientService:
        return QdrantClientService(url=url or config.QDRANT_URL, api_key=api_key)


def default_factory() -> StorageFactory:
    """The factory the OSS gateway uses (a cloud overlay overrides this)."""
    return StorageFactory()
