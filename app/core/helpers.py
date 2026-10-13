import asyncio
import inspect
import logging
import time
import uuid
from typing import Any

import redis
from fastapi import BackgroundTasks

from app.core.config import config

logger = logging.getLogger(__name__)

_broker_status_cache: dict[str, tuple[bool, float]] = {}
_CACHE_TTL_SECONDS = 5.0


def clear_broker_online_cache() -> None:
    """Clear cached broker availability status."""
    _broker_status_cache.clear()


def is_celery_broker_online(force_refresh: bool = False) -> bool:
    """Check if the Celery Redis broker is reachable, caching status for 5 seconds."""
    cache_key = config.CELERY_BROKER_URL
    now = time.monotonic()

    if not force_refresh and cache_key in _broker_status_cache:
        status, timestamp = _broker_status_cache[cache_key]
        if now - timestamp < _CACHE_TTL_SECONDS:
            return status

    client = None
    online = False
    try:
        client = redis.from_url(
            config.CELERY_BROKER_URL, socket_timeout=0.2, socket_connect_timeout=0.2
        )
        client.ping()
        online = True
    except redis.RedisError, OSError:
        online = False
    finally:
        if client is not None:
            try:
                client.close()
            except redis.RedisError, OSError:
                pass

    _broker_status_cache[cache_key] = (online, now)
    return online


def dispatch_task(
    celery_task: Any,
    async_job: Any,
    *args: Any,
    background_tasks: BackgroundTasks | None = None,
    task_name: str = "task",
    task_id_prefix: str = "bg-",
    **kwargs: Any,
) -> tuple[str, bool]:
    """
    Dispatch task to Celery worker if broker is online, with fallback to background tasks or direct execution.

    Returns (task_id, dispatched_to_celery).
    """
    task_id = None
    dispatched_to_celery = False

    if is_celery_broker_online():
        logger.info("Dispatching to Celery worker")
        try:
            async_result = celery_task.delay(*args, **kwargs)
            task_id = async_result.id
            dispatched_to_celery = True
            logger.info("Dispatched %s %s to Celery worker", task_name, task_id)
        except Exception as celery_err:
            logger.warning("Celery dispatch failed: %s", celery_err)

    if not dispatched_to_celery:
        logger.info("Celery broker is not online, dispatching to background tasks")
        task_id = f"{task_id_prefix}{uuid.uuid4()}"
        if background_tasks:
            background_tasks.add_task(async_job, *args, **kwargs)
        else:
            if inspect.iscoroutinefunction(async_job):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(async_job(*args, **kwargs))
                except RuntimeError:
                    asyncio.run(async_job(*args, **kwargs))
            else:
                async_job(*args, **kwargs)

    return task_id, dispatched_to_celery
