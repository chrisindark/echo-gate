import asyncio
import logging
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.constants import EchoGateHeaders
from app.core.database import SessionFactory, SessionLocal
from app.modules.llm_quota.llm_quota_model import LlmQuotaRule
from app.modules.redis.redis_service import RedisService

logger = logging.getLogger(__name__)

#: Counter window used by the RPM/TPM Redis keys. Advertised in reset headers.
QUOTA_WINDOW_SECONDS = 60


@dataclass(frozen=True)
class QuotaStatus:
    """Per-request view of the caller's rate-limit budget.

    Returned by :meth:`LlmQuotaService.check_quota` so the guard can advertise
    the remaining budget to downstream clients instead of only failing them.
    ``remaining_*`` already accounts for the request being served.
    """

    limit_requests: int
    remaining_requests: int
    reset_requests_seconds: int
    limit_tokens: int
    remaining_tokens: int
    reset_tokens_seconds: int

    def to_headers(self) -> dict[str, str]:
        return {
            EchoGateHeaders.RATE_LIMIT_LIMIT_REQUESTS: str(self.limit_requests),
            EchoGateHeaders.RATE_LIMIT_REMAINING_REQUESTS: str(self.remaining_requests),
            EchoGateHeaders.RATE_LIMIT_RESET_REQUESTS: str(self.reset_requests_seconds),
            EchoGateHeaders.RATE_LIMIT_LIMIT_TOKENS: str(self.limit_tokens),
            EchoGateHeaders.RATE_LIMIT_REMAINING_TOKENS: str(self.remaining_tokens),
            EchoGateHeaders.RATE_LIMIT_RESET_TOKENS: str(self.reset_tokens_seconds),
        }

    def retry_after(self, *, tokens: bool = False) -> int:
        """Seconds a throttled client should wait before retrying."""
        reset = self.reset_tokens_seconds if tokens else self.reset_requests_seconds
        return max(int(reset), 1)


class LlmQuotaService:
    redis_service: RedisService
    _session_factory: SessionFactory | None
    _db_session: Session | None

    def __init__(
        self,
        redis_service: RedisService,
        session_factory: SessionFactory | Session | None = None,
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
    def _get_session(self) -> Generator[Session, None, None]:
        if self._db_session is not None:
            yield self._db_session
        elif self._session_factory is not None:
            with self._session_factory() as s:
                yield s
        else:
            with SessionLocal() as s:
                yield s

    async def get_applicable_limits(
        self,
        provider: str | None = None,
        model: str | None = None,
        user: str | None = None,
        tenant_id: str | None = None,
    ) -> tuple[int | None, int | None]:
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

            max_rpm: int | None = None
            max_tpm: int | None = None

            for rule in rules:
                rule_max_rpm = getattr(rule, "max_rpm", None)
                if rule_max_rpm is not None and (
                    max_rpm is None or rule_max_rpm < max_rpm
                ):
                    max_rpm = int(rule_max_rpm)
                rule_max_tpm = getattr(rule, "max_tpm", None)
                if rule_max_tpm is not None and (
                    max_tpm is None or rule_max_tpm < max_tpm
                ):
                    max_tpm = int(rule_max_tpm)

            return max_rpm, max_tpm

    async def check_quota(
        self,
        api_key: str | None,
        provider: str | None = None,
        model: str | None = None,
        user: str | None = None,
        tenant_id: str | None = None,
        bucket_id: str | None = None,
    ) -> QuotaStatus:
        """Enforce RPM/TPM/budget limits for one request.

        ``bucket_id`` selects who shares a rate-limit counter. A keyed caller
        passes its API key (or leaves ``bucket_id`` unset), so two requests
        holding the same key share a bucket. Anonymous calls must pass a
        per-caller ``bucket_id`` fingerprint; without one they would all land
        in the historical shared ``rpm:None:...`` bucket and a single keyless
        caller could exhaust the default 10 RPM for every anonymous caller.
        ``api_key`` remains the identity for the paid ``budget:`` counter.

        Returns the post-increment budget so callers can advertise it in
        ``X-RateLimit-*`` headers. Raises 429/402 with the same headers when the
        request is refused, so a throttled client is told what to wait for.
        """
        max_rpm, max_tpm = await self.get_applicable_limits(
            provider, model, user, tenant_id
        )
        if max_rpm is None:
            max_rpm = 10  # Default RPM limit # move to config

        if max_tpm is None:
            max_tpm = 20000  # Default TPM limit # move to config

        redis_suffix = (
            f"{bucket_id or api_key}:{provider or 'any'}:{model or 'any'}:"
            f"{user or 'any'}"
        )
        rpm_key = f"rpm:{redis_suffix}"
        tpm_key = f"tpm:{redis_suffix}"

        try:
            budget, rpm, tpm = await asyncio.gather(
                self.redis_service.get(f"budget:{api_key}"),
                self.redis_service.incr_with_ttl(rpm_key, QUOTA_WINDOW_SECONDS),
                self.redis_service.get(tpm_key),
            )
        except Exception as e:
            logger.error(f"Error in check_quota asyncio.gather: {e}")
            # gracefully degrade: infinite budget, RPM and TPM if redis is down
            budget, rpm, tpm = None, 0, "0"

        if rpm is None:
            rpm = 0

        if tpm is None:
            tpm = 0

        # The window resets when the Redis keys expire. Read the live TTLs after
        # the increment so the advertised reset matches what Redis enforces; a
        # failure here only costs precision, never the request.
        reset_rpm_seconds, reset_tpm_seconds = await self._read_window_resets(
            rpm_key, tpm_key
        )

        try:
            used_tpm = int(float(tpm))
        except (TypeError, ValueError):
            logger.error(f"Error in check_quota TPM and MAX_TPM: {tpm} | {max_tpm}")
            used_tpm = 0

        quota = QuotaStatus(
            limit_requests=int(max_rpm),
            remaining_requests=max(0, int(max_rpm) - int(rpm)),
            reset_requests_seconds=reset_rpm_seconds,
            limit_tokens=int(max_tpm),
            remaining_tokens=max(0, int(max_tpm) - used_tpm),
            reset_tokens_seconds=reset_tpm_seconds,
        )

        if rpm > max_rpm:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded (Limit: {max_rpm} RPM)",
                headers={
                    **quota.to_headers(),
                    "Retry-After": str(quota.retry_after()),
                },
            )

        if used_tpm > max_tpm:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Token rate limit exceeded (Limit: {max_tpm} TPM)",
                headers={
                    **quota.to_headers(),
                    "Retry-After": str(quota.retry_after(tokens=True)),
                },
            )

        try:
            budget_val = float(budget or "0")
        except (TypeError, ValueError):
            budget_val = 0

        if budget is not None and budget_val <= 0:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Payment Required: Budget exhausted",
                headers=quota.to_headers(),
            )

        return quota

    async def _read_window_resets(self, rpm_key: str, tpm_key: str) -> tuple[int, int]:
        """Seconds until the RPM/TPM windows reset, defaulting to a full window."""
        ttl_fn = getattr(self.redis_service, "remaining_ttl", None)
        if ttl_fn is None:
            return QUOTA_WINDOW_SECONDS, QUOTA_WINDOW_SECONDS
        try:
            rpm_ttl, tpm_ttl = await asyncio.gather(ttl_fn(rpm_key), ttl_fn(tpm_key))
        except Exception as e:
            logger.error(f"Error reading quota window TTLs: {e}")
            return QUOTA_WINDOW_SECONDS, QUOTA_WINDOW_SECONDS

        def _clamp(value: object) -> int:
            # Redis returns -2 for a missing key and -1 for a key without a TTL;
            # both mean "the window is not counting down", i.e. a full window.
            try:
                seconds = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return QUOTA_WINDOW_SECONDS
            if seconds < 0:
                return QUOTA_WINDOW_SECONDS
            return min(seconds, QUOTA_WINDOW_SECONDS)

        return _clamp(rpm_ttl), _clamp(tpm_ttl)

    async def record_tokens(
        self,
        api_key: str,
        total_tokens: int,
        provider: str | None = None,
        model: str | None = None,
        user_id: str | None = None,
    ) -> None:
        redis_suffix = (
            f"{api_key}:{provider or 'any'}:{model or 'any'}:{user_id or 'any'}"
        )
        tpm_key = f"tpm:{redis_suffix}"

        # INCRBY + EXPIRE (NX) are pipelined so the key can never be left with no
        # TTL, which would otherwise let the token window grow unbounded forever.
        await self.redis_service.incrby_with_ttl(tpm_key, total_tokens, 60)
