import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from fastapi import HTTPException
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import config

logger = logging.getLogger(__name__)

T = TypeVar("T")


def is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, asyncio.CancelledError):
        return False
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    if isinstance(exc, HTTPException):
        return exc.status_code not in (400, 401, 403, 500, 502, 503, 504)
    return True


async def execute_with_retry(
    coro_fn: Callable[..., Coroutine[Any, Any, T]],
    *args: Any,
    timeout_seconds: float,
    max_retries: int = 0,
    initial_backoff: float | None = None,
    max_backoff: float | None = None,
    retry_predicate: Callable[[BaseException], bool] | None = None,
    **kwargs: Any,
) -> T:
    """
    Executes an async callable with a per-attempt timeout and exponential jittered retries.
    """
    initial = (
        initial_backoff
        if initial_backoff is not None
        else config.RETRY_BACKOFF_INITIAL_SECONDS
    )
    max_wait = (
        max_backoff if max_backoff is not None else config.RETRY_BACKOFF_MAX_SECONDS
    )
    predicate = retry_predicate or is_retryable_exception

    retryer = AsyncRetrying(
        stop=stop_after_attempt(max_retries + 1)
        if max_retries > 0
        else stop_after_attempt(1),
        wait=wait_exponential_jitter(
            initial=initial,
            max=max_wait,
        ),
        retry=retry_if_exception(predicate),
        reraise=True,
    )

    async for attempt in retryer:
        with attempt:
            return await asyncio.wait_for(
                coro_fn(*args, **kwargs),
                timeout=timeout_seconds,
            )

    raise RuntimeError("execute_with_retry exhausted without returning or raising")
