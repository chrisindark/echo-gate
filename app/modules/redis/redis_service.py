import json
import logging
import os
from typing import Any

from redis import asyncio as aioredis

logger = logging.getLogger(__name__)


class RedisService:
    def __init__(self, redis_url: str | None = None):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis = aioredis.from_url(self.redis_url, decode_responses=True)

        self.ttl = int(os.getenv("REDIS_TTL", 3600))  # Default 1 hour TTL
        logger.info(f"Connecting to Redis at {self.redis_url}")

    async def ping(self):
        try:
            if await self.redis.ping():
                logger.info("Redis connection is successful")
        except aioredis.ConnectionError:
            logger.info("Error in Redis connection")

    async def get(self, key: str) -> dict[str, Any] | None:
        """Retrieve an exact match from Redis cache."""
        try:
            cached_data = await self.redis.get(key)
            if cached_data:
                logger.info("Redis exact cache hit!")
                return json.loads(cached_data)
        except Exception as e:
            logger.error(f"Failed to get data from Redis: {e}")
        return None

    async def set(
        self, key: str, response: dict[str, Any], ttl: int | None = None
    ) -> None:
        """Cache the exact response for a prompt in Redis."""
        try:
            expire_time = ttl if ttl is not None else self.ttl
            return await self.redis.set(key, json.dumps(response), ex=expire_time)
            logger.info("Successfully saved exact match to Redis")
        except Exception as e:
            logger.error(f"Failed to save data to Redis: {e}")
        return None

    async def close(self):
        """Close Redis connection."""
        await self.redis.close()
