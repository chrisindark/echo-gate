import os
import json
import logging
from typing import Optional, Dict, Any
from redis import asyncio as aioredis

logger = logging.getLogger(__name__)

class RedisService:
    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis = aioredis.from_url(self.redis_url, decode_responses=True)
        self.ttl = int(os.getenv("REDIS_TTL", 3600))  # Default 1 hour TTL
        logger.info(f"Connecting to Redis at {self.redis_url}")

    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve an exact match from Redis cache."""
        try:
            cached_data = await self.redis.get(key)
            if cached_data:
                logger.info("Redis exact cache hit!")
                return json.loads(cached_data)
        except Exception as e:
            logger.error(f"Failed to get data from Redis: {e}")
        return None

    async def set(self, key: str, response: Dict[str, Any], ttl: Optional[int] = None) -> None:
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
