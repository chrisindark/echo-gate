from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.llm_quota.llm_quota_model import LlmQuotaRule
from app.modules.llm_quota.llm_quota_service import (
    QUOTA_WINDOW_SECONDS,
    LlmQuotaService,
)
from app.modules.redis.redis_service import RedisService


@pytest.fixture
def mock_redis():
    mock = MagicMock(spec=RedisService)
    mock.incr_with_ttl = AsyncMock(return_value=1)
    mock.remaining_ttl = AsyncMock(return_value=30)
    mock.incrby_with_ttl = AsyncMock(return_value=500)
    return mock


@pytest.fixture
def mock_session():
    return MagicMock(spec=Session)


@pytest.mark.asyncio
async def test_llm_quota_service_init_and_applicable_limits(mock_redis, mock_session):
    svc = LlmQuotaService(redis_service=mock_redis, session_factory=mock_session)

    # Session context manager
    with svc._get_session() as s:
        assert s == mock_session

    # get_applicable_limits
    r1 = LlmQuotaRule(max_rpm=100, max_tpm=50000, is_active=True)
    r2 = LlmQuotaRule(max_rpm=50, max_tpm=80000, is_active=True)
    mock_session.query.return_value.filter.return_value.filter.return_value.filter.return_value.filter.return_value.filter.return_value.all.return_value = [
        r1,
        r2,
    ]

    rpm, tpm = await svc.get_applicable_limits(
        provider="openai", model="gpt-4o", user="user1", tenant_id="tenant1"
    )
    assert rpm == 50
    assert tpm == 50000


@pytest.mark.asyncio
async def test_llm_quota_service_check_quota_rpm_limit(mock_redis, mock_session):
    svc = LlmQuotaService(redis_service=mock_redis, db_session=mock_session)
    mock_session.query.return_value.filter.return_value.all.return_value = [
        LlmQuotaRule(max_rpm=10, max_tpm=20000, is_active=True)
    ]

    # RPM exceeded: incr_with_ttl returns 11 (> 10)
    mock_redis.get = AsyncMock(return_value="0")
    mock_redis.incr_with_ttl = AsyncMock(return_value=11)
    with pytest.raises(HTTPException) as ctx:
        await svc.check_quota(api_key="key1", provider="openai", model="gpt-4o-mini")
    assert ctx.value.status_code == 429
    assert "Rate limit exceeded (Limit: 10 RPM)" in ctx.value.detail


@pytest.mark.asyncio
async def test_llm_quota_service_check_quota_tpm_limit(mock_redis, mock_session):
    svc = LlmQuotaService(redis_service=mock_redis, db_session=mock_session)
    mock_query = mock_session.query.return_value
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [
        LlmQuotaRule(max_rpm=100, max_tpm=1000, is_active=True)
    ]

    mock_redis.incr_with_ttl = AsyncMock(return_value=1)
    mock_redis.remaining_ttl = AsyncMock(return_value=25)

    # If tpm key in redis is "1500" (> 1000 limit)
    async def mock_get(key):
        if "tpm" in key:
            return "1500"
        return None

    mock_redis.get = AsyncMock(side_effect=mock_get)

    with pytest.raises(HTTPException) as ctx:
        await svc.check_quota(api_key="key1", provider="openai", model="gpt-4o-mini")
    assert ctx.value.status_code == 429
    assert "Token rate limit exceeded (Limit: 1000 TPM)" in ctx.value.detail


@pytest.mark.asyncio
async def test_llm_quota_service_check_quota_budget_exhausted(mock_redis, mock_session):
    svc = LlmQuotaService(redis_service=mock_redis, db_session=mock_session)
    mock_session.query.return_value.filter.return_value.all.return_value = []

    mock_redis.incr_with_ttl = AsyncMock(return_value=1)

    # Budget exhausted <= 0
    async def mock_get_zero_budget(key):
        if "budget" in key:
            return "0.0"
        return "0"

    mock_redis.get = AsyncMock(side_effect=mock_get_zero_budget)

    with pytest.raises(HTTPException) as ctx:
        await svc.check_quota(api_key="key1", provider="openai", model="gpt-4o-mini")
    assert ctx.value.status_code == 402
    assert "Budget exhausted" in ctx.value.detail

    # Invalid budget string falls back to 0 -> 402
    async def mock_get_invalid_budget(key):
        if "budget" in key:
            return "not-a-number"
        return "0"

    mock_redis.get = AsyncMock(side_effect=mock_get_invalid_budget)
    with pytest.raises(HTTPException) as ctx:
        await svc.check_quota(api_key="key1", provider="openai", model="gpt-4o-mini")
    assert ctx.value.status_code == 402

    # Graceful degradation on redis exception
    mock_redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
    quota = await svc.check_quota(
        api_key="key1", provider="openai", model="gpt-4o-mini"
    )
    assert quota is not None


@pytest.mark.asyncio
async def test_llm_quota_service_window_resets_and_record_tokens(
    mock_redis, mock_session
):
    svc = LlmQuotaService(redis_service=mock_redis, db_session=mock_session)

    # Redis remaining_ttl error
    mock_redis.remaining_ttl = AsyncMock(side_effect=Exception("redis down"))
    rpm_reset, tpm_reset = await svc._read_window_resets("rpm_k", "tpm_k")
    assert rpm_reset == QUOTA_WINDOW_SECONDS
    assert tpm_reset == QUOTA_WINDOW_SECONDS

    # Negative or non-int TTL
    mock_redis.remaining_ttl = AsyncMock(side_effect=[-2, "invalid"])
    r1, r2 = await svc._read_window_resets("rpm_k", "tpm_k")
    assert r1 == QUOTA_WINDOW_SECONDS
    assert r2 == QUOTA_WINDOW_SECONDS

    # record_tokens
    await svc.record_tokens(
        "key_test", 150, provider="openai", model="gpt-4o-mini", user_id="u1"
    )
    mock_redis.incrby_with_ttl.assert_awaited_once_with(
        "tpm:key_test:openai:gpt-4o-mini:u1", 150, 60
    )
