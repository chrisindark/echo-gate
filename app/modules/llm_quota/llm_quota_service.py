import asyncio
import logging
from collections.abc import Callable
from contextlib import contextmanager

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.modules.llm_quota.llm_quota_model import LlmQuotaRule
from app.modules.redis.redis_service import RedisService

logger = logging.getLogger(__name__)


class LlmQuotaService:
    def __init__(
        self,
        redis_service: RedisService,
        session_factory: Callable[[], Session] | Session | None = None,
        db_session: Session | None = None,
    ):
        self.redis_service = redis_service
        if isinstance(session_factory, Session):
            db_session = session_factory
            session_factory = None

        if session_factory is None and db_session is None:
            session_factory = SessionLocal

        self._session_factory = session_factory
        self._db_session = db_session

    @contextmanager
    def _get_session(self):
        if self._db_session is not None:
            yield self._db_session
        else:
            with self._session_factory() as s:
                yield s

    async def get_applicable_limits(
        self,
        provider: str | None = None,
        model: str | None = None,
        user: str | None = None,
        tenant_id: str | None = None,
    ):
        with self._get_session() as session:
            query = session.query(LlmQuotaRule).filter(LlmQuotaRule.is_active.is_(True))

            if provider:
                query = query.filter(
                    or_(
                        LlmQuotaRule.provider.is_(None),
                        LlmQuotaRule.provider == provider,
                    )
                )
            if model:
                query = query.filter(
                    or_(LlmQuotaRule.model.is_(None), LlmQuotaRule.model == model)
                )
            if user:
                query = query.filter(
                    or_(LlmQuotaRule.user_id.is_(None), LlmQuotaRule.user_id == user)
                )
            if tenant_id:
                query = query.filter(
                    or_(
                        LlmQuotaRule.tenant_id.is_(None),
                        LlmQuotaRule.tenant_id == tenant_id,
                    )
                )

            rules = query.all()

            max_rpm = None
            max_tpm = None

            for rule in rules:
                if rule.max_rpm is not None and (
                    max_rpm is None or rule.max_rpm < max_rpm
                ):
                    max_rpm = rule.max_rpm
                if rule.max_tpm is not None and (
                    max_tpm is None or rule.max_tpm < max_tpm
                ):
                    max_tpm = rule.max_tpm

            return max_rpm, max_tpm

    async def check_quota(
        self,
        api_key: str,
        provider: str | None = None,
        model: str | None = None,
        user: str | None = None,
        tenant_id: str | None = None,
    ) -> bool:
        max_rpm, max_tpm = await self.get_applicable_limits(
            provider, model, user, tenant_id
        )
        if max_rpm is None:
            max_rpm = 10  # Default RPM limit # move to config

        if max_tpm is None:
            max_tpm = 20000  # Default TPM limit # move to config

        redis_suffix = f"{api_key}:{provider or 'any'}:{model or 'any'}:{user or 'any'}"
        rpm_key = f"rpm:{redis_suffix}"
        tpm_key = f"tpm:{redis_suffix}"

        try:
            budget, rpm, tpm = await asyncio.gather(
                self.redis_service.get(f"budget:{api_key}"),
                self.redis_service.incr(rpm_key),
                self.redis_service.get(tpm_key),
            )
        except Exception as e:
            logger.error(f"Error in check_quota asyncio.gather: {e}")
            # gracefully degrade: infinite budget, RPM and TPM if redis is down
            budget, rpm, tpm = None, 0, "0"

        if rpm is None:
            rpm = 0

        if rpm == 1:
            await self.redis_service.expire(rpm_key, 60)

        if rpm > max_rpm:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded (Limit: {max_rpm} RPM)",
            )

        if tpm is None:
            tpm = 0

        try:
            if float(tpm) > max_tpm:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Token rate limit exceeded (Limit: {max_tpm} TPM)",
                )
        except (TypeError, ValueError):
            logger.error(f"Error in check_quota TPM and MAX_TPM: {tpm} | {max_tpm}")

        try:
            budget_val = float(budget or "0")
        except (TypeError, ValueError):
            budget_val = 0

        if budget is not None and budget_val <= 0:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Payment Required: Budget exhausted",
            )

        return True

    async def record_tokens(
        self,
        api_key: str,
        total_tokens: int,
        provider: str | None = None,
        model: str | None = None,
        user_id: str | None = None,
    ):
        redis_suffix = (
            f"{api_key}:{provider or 'any'}:{model or 'any'}:{user_id or 'any'}"
        )
        tpm_key = f"tpm:{redis_suffix}"

        tpm = await self.redis_service.incrby(tpm_key, total_tokens)

        # If it's the first time setting this key, expire it in 60 seconds
        if tpm == total_tokens:
            await self.redis_service.expire(tpm_key, 60)
