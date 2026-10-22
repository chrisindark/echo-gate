import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import HTTPException

from app.core.config import config

logger = logging.getLogger(__name__)


class BulkheadLimitExceededException(HTTPException):
    def __init__(self, message: str, retry_after: int = 1):
        super().__init__(
            status_code=429,
            detail=message,
            headers={"Retry-After": str(retry_after)},
        )


class BulkheadManager:
    """Manages concurrent in-flight requests across the gateway and per provider.

    Enforces bulkheads so a slow or stalling upstream provider cannot consume all
    gateway resources or starve requests intended for other providers.
    """

    def __init__(
        self,
        global_max_concurrency: int | None = None,
        provider_max_concurrency: int | None = None,
    ) -> None:
        self._global_max = global_max_concurrency
        self._provider_max = provider_max_concurrency
        self._global_semaphore: asyncio.Semaphore | None = None
        self._provider_semaphores: dict[str, asyncio.Semaphore] = {}
        self._active_global: int = 0
        self._active_per_provider: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def _get_global_limit(self) -> int:
        if self._global_max is not None:
            return self._global_max
        return getattr(config, "GLOBAL_MAX_CONCURRENCY", 100)

    def _get_provider_limit(self) -> int:
        if self._provider_max is not None:
            return self._provider_max
        return getattr(config, "PROVIDER_MAX_CONCURRENCY", 25)

    def _ensure_global_semaphore(self) -> asyncio.Semaphore:
        limit = self._get_global_limit()
        if (
            self._global_semaphore is None
            or self._global_semaphore._value + self._active_global != limit
        ):
            self._global_semaphore = asyncio.Semaphore(limit)
        return self._global_semaphore

    def _get_provider_semaphore(self, service_name: str) -> asyncio.Semaphore:
        normalized = service_name.strip().lower()
        limit = self._get_provider_limit()
        if normalized not in self._provider_semaphores:
            self._provider_semaphores[normalized] = asyncio.Semaphore(limit)
        return self._provider_semaphores[normalized]

    @property
    def active_global_count(self) -> int:
        return self._active_global

    def active_provider_count(self, service_name: str) -> int:
        return self._active_per_provider.get(service_name.strip().lower(), 0)

    @asynccontextmanager
    async def acquire(
        self, service_name: str, timeout: float = 0.0
    ) -> AsyncGenerator[None, None]:
        """Acquire both global and provider-level concurrency permits.

        If either limit is reached and cannot be acquired within timeout,
        raises BulkheadLimitExceededException (HTTP 429).
        """
        global_sem = self._ensure_global_semaphore()
        provider_sem = self._get_provider_semaphore(service_name)
        normalized = service_name.strip().lower()

        # Try to acquire global permit
        global_acquired = False
        try:
            if timeout <= 0.0:
                # Immediate non-blocking check
                if global_sem.locked():
                    raise BulkheadLimitExceededException(
                        "Global gateway concurrency limit reached. Load shedding active."
                    )
                await global_sem.acquire()
            else:
                try:
                    await asyncio.wait_for(global_sem.acquire(), timeout=timeout)
                except TimeoutError:
                    raise BulkheadLimitExceededException(
                        "Global gateway concurrency limit reached. Load shedding active."
                    )
            global_acquired = True
            self._active_global += 1

            # Try to acquire provider permit
            provider_acquired = False
            try:
                if timeout <= 0.0:
                    if provider_sem.locked():
                        raise BulkheadLimitExceededException(
                            f"Concurrency limit reached for provider '{normalized}'. Upstream bulkhead saturated."
                        )
                    await provider_sem.acquire()
                else:
                    try:
                        await asyncio.wait_for(provider_sem.acquire(), timeout=timeout)
                    except TimeoutError:
                        raise BulkheadLimitExceededException(
                            f"Concurrency limit reached for provider '{normalized}'. Upstream bulkhead saturated."
                        )
                provider_acquired = True
                self._active_per_provider[normalized] = (
                    self._active_per_provider.get(normalized, 0) + 1
                )

                yield

            finally:
                if provider_acquired:
                    self._active_per_provider[normalized] = max(
                        0, self._active_per_provider.get(normalized, 1) - 1
                    )
                    provider_sem.release()
        finally:
            if global_acquired:
                self._active_global = max(0, self._active_global - 1)
                global_sem.release()

    def reset(self) -> None:
        self._global_semaphore = None
        self._provider_semaphores.clear()
        self._active_global = 0
        self._active_per_provider.clear()


bulkhead_manager = BulkheadManager()
