from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis import asyncio as aioredis

from app.modules.redis.redis_service import RedisService


@pytest.fixture
def mock_aioredis():
    mock = MagicMock()
    mock.ping = AsyncMock(return_value=True)
    mock.get = AsyncMock(return_value="cached_value")
    mock.set = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=1)
    mock.expire = AsyncMock(return_value=True)
    mock.incr = AsyncMock(return_value=2)
    mock.decr = AsyncMock(return_value=1)
    mock.incrby = AsyncMock(return_value=5)
    mock.decrby = AsyncMock(return_value=3)
    mock.ttl = AsyncMock(return_value=60)
    mock.aclose = AsyncMock()

    # pipeline mock
    pipe_mock = MagicMock()
    pipe_mock.incr = MagicMock()
    pipe_mock.incrby = MagicMock()
    pipe_mock.expire = MagicMock()
    pipe_mock.execute = AsyncMock(return_value=[10, True])
    mock.pipeline.return_value = pipe_mock

    return mock


@pytest.mark.asyncio
async def test_redis_service_ping(mock_aioredis):
    with patch("redis.asyncio.from_url", return_value=mock_aioredis):
        service = RedisService()
        await service.ping()
        mock_aioredis.ping.assert_awaited_once()

        # Connection error branch
        mock_aioredis.ping = AsyncMock(
            side_effect=aioredis.ConnectionError("Redis down")
        )
        await service.ping()


@pytest.mark.asyncio
async def test_redis_service_get(mock_aioredis):
    with patch("redis.asyncio.from_url", return_value=mock_aioredis):
        service = RedisService()

        # Cache hit
        res = await service.get("key1")
        assert res == "cached_value"

        # Cache miss
        mock_aioredis.get = AsyncMock(return_value=None)
        res_miss = await service.get("key2")
        assert res_miss is None

        # Exception & degradation
        mock_aioredis.get = AsyncMock(side_effect=Exception("Read failure"))
        with patch(
            "app.modules.redis.redis_service.mark_cache_degraded"
        ) as mock_degrade:
            res_err = await service.get("key3")
            assert res_err is None
            mock_degrade.assert_called_once_with("redis_read_failed")


@pytest.mark.asyncio
async def test_redis_service_set(mock_aioredis):
    with patch("redis.asyncio.from_url", return_value=mock_aioredis):
        service = RedisService()

        # Set default TTL
        res = await service.set("k", "v")
        assert res is True
        mock_aioredis.set.assert_awaited_with("k", "v", ex=service.ttl)

        # Set custom TTL
        await service.set("k2", "v2", ttl=100)
        mock_aioredis.set.assert_awaited_with("k2", "v2", ex=100)

        # Exception
        mock_aioredis.set = AsyncMock(side_effect=Exception("write error"))
        res_err = await service.set("k3", "v3")
        assert res_err is None


@pytest.mark.asyncio
async def test_redis_service_delete(mock_aioredis):
    with patch("redis.asyncio.from_url", return_value=mock_aioredis):
        service = RedisService()
        assert await service.delete("k") == 1

        mock_aioredis.delete = AsyncMock(side_effect=Exception("del error"))
        assert await service.delete("k") is None


@pytest.mark.asyncio
async def test_redis_service_close(mock_aioredis):
    with patch("redis.asyncio.from_url", return_value=mock_aioredis):
        service = RedisService()
        await service.close()
        mock_aioredis.aclose.assert_awaited_once()

        # Fallback to close if no aclose
        legacy_mock = MagicMock(spec=["close"])
        legacy_mock.close = AsyncMock()
        service.redis = legacy_mock
        await service.close()
        legacy_mock.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_redis_service_expire_and_ttl(mock_aioredis):
    with patch("redis.asyncio.from_url", return_value=mock_aioredis):
        service = RedisService()
        assert await service.expire("k", 30) is True
        assert await service.remaining_ttl("k") == 60

        mock_aioredis.expire = AsyncMock(side_effect=Exception("expire err"))
        assert await service.expire("k", 30) is None

        mock_aioredis.ttl = AsyncMock(side_effect=Exception("ttl err"))
        assert await service.remaining_ttl("k") is None


@pytest.mark.asyncio
async def test_redis_service_increments_and_decrements(mock_aioredis):
    with patch("redis.asyncio.from_url", return_value=mock_aioredis):
        service = RedisService()

        # incr & decr
        assert await service.incr("counter") == 2
        assert await service.decr("counter") == 1
        assert await service.incrby("counter", 3) == 5
        assert await service.decrby("counter", 2) == 3

        # incr_with_ttl & incrby_with_ttl
        assert await service.incr_with_ttl("c", 60) == 10
        assert await service.incrby_with_ttl("c", 5, 60) == 10

        # Exception branches
        mock_aioredis.incr = AsyncMock(side_effect=Exception("incr err"))
        assert await service.incr("counter") is None

        mock_aioredis.decr = AsyncMock(side_effect=Exception("decr err"))
        assert await service.decr("counter") is None

        mock_aioredis.incrby = AsyncMock(side_effect=Exception("incrby err"))
        assert await service.incrby("counter", 3) is None

        mock_aioredis.decrby = AsyncMock(side_effect=Exception("decrby err"))
        assert await service.decrby("counter", 2) is None

        pipe_fail = MagicMock()
        pipe_fail.execute = AsyncMock(side_effect=Exception("pipe err"))
        mock_aioredis.pipeline.return_value = pipe_fail

        assert await service.incr_with_ttl("c", 60) is None
        assert await service.incrby_with_ttl("c", 5, 60) is None
