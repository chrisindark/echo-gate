import logging
import os

from redis import asyncio as aioredis

from app.core.logger import log_latency

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
        except aioredis.ConnectionError as e:
            logger.error(f"Error in Redis connection: {e}")

    @log_latency()
    async def get(self, key: str) -> str | None:
        """Retrieve an exact match from Redis cache."""
        try:
            cached_data = await self.redis.get(key)
            if cached_data:
                logger.info("Redis exact cache hit!")
                return cached_data
        except Exception as e:
            logger.error(f"Failed to get data from Redis: {e}")

        return None

    @log_latency()
    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        """Cache the exact response for a prompt in Redis."""
        try:
            expire_time = ttl if ttl is not None else self.ttl
            logger.info("Successfully saved exact match to Redis")
            response = await self.redis.set(key, value, ex=expire_time)
            return response
        except Exception as e:
            logger.error(f"Failed to save data to Redis: {e}")

        return None

    async def close(self):
        """Close Redis connection."""
        await self.redis.close()

    async def expire(self, key: str, seconds: int):
        try:
            return await self.redis.expire(key, seconds)
        except Exception as e:
            logger.error(f"Failed to expire key in Redis: {e}")

        return None

    async def incr(self, key: str):
        try:
            return await self.redis.incr(key)
        except Exception as e:
            logger.error(f"Failed to increment key in Redis: {e}")

        return None

    async def incrby(self, key: str, amount: int):
        try:
            return await self.redis.incrby(key, amount)
        except Exception as e:
            logger.error(f"Failed to increment key in Redis: {e}")

        return None

    async def decr(self, key: str):
        try:
            return await self.redis.decr(key)
        except Exception as e:
            logger.error(f"Failed to decrement key in Redis: {e}")

        return None

    async def decrby(self, key: str, amount: int):
        try:
            return await self.redis.decrby(key, amount)
        except Exception as e:
            logger.error(f"Failed to decrement key in Redis: {e}")

        return None
