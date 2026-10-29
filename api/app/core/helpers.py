import asyncio
import inspect
import logging
import math
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
    except (redis.RedisError, OSError):
        online = False
    finally:
        if client is not None:
            try:
                client.close()
            except (redis.RedisError, OSError):
                pass

    _broker_status_cache[cache_key] = (online, now)
    return online


# --- Token counting -------------------------------------------------------
#
# `tiktoken` is a Rust BPE tokenizer with no model weights, but its encoding
# files are downloaded on first use and cached under TIKTOKEN_CACHE_DIR. An
# air-gapped deployment (or a test run without network) must still answer, so a
# failure degrades to a characters-per-token estimate instead of raising. The
# encoding name is returned alongside the count so an approximation is never
# presented as exact.

_CHARS_PER_TOKEN = 4
_TOKENIZER_CACHE: dict[str, Any] = {}

# Model-family prefixes whose tokenizer is a different BPE vocabulary.
_O200K_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "o4")


def tokenizer_name_for_model(model: str | None) -> str:
    """BPE encoding used for a model, or ``heuristic`` when tiktoken is absent."""
    normalized = (model or "").strip().lower()
    if normalized.startswith(_O200K_PREFIXES):
        return "o200k_base"
    return "cl100k_base"


def get_tokenizer(model: str | None) -> Any | None:
    """Return the cached tiktoken encoding for a model, or ``None`` if unavailable."""
    encoding_name = tokenizer_name_for_model(model)
    if encoding_name in _TOKENIZER_CACHE:
        return _TOKENIZER_CACHE[encoding_name]

    encoding = None
    try:
        import tiktoken

        encoding = tiktoken.get_encoding(encoding_name)
    except Exception as exc:  # import error, download failure, corrupt cache
        logger.warning(
            "tiktoken encoding %r unavailable (%s); falling back to ~%s chars/token",
            encoding_name,
            exc,
            _CHARS_PER_TOKEN,
        )
    _TOKENIZER_CACHE[encoding_name] = encoding
    return encoding


def reset_tokenizer_cache() -> None:
    """Drop cached encoders (used by tests that simulate an offline engine)."""
    _TOKENIZER_CACHE.clear()


def tokenizer_label(model: str | None) -> str:
    """Human-readable name of the counter actually used for ``model``."""
    return (
        tokenizer_name_for_model(model)
        if get_tokenizer(model) is not None
        else f"heuristic ({_CHARS_PER_TOKEN} chars/token)"
    )


def count_tokens(text: str, model: str | None = None) -> int:
    """Count BPE tokens for ``text``, falling back to a length estimate.

    ``model`` only selects the vocabulary; unknown models use ``cl100k_base``,
    which is also the closest approximation for the non-OpenAI providers whose
    tokenizers are not public.
    """
    if not text:
        return 0

    encoding = get_tokenizer(model)
    if encoding is not None:
        try:
            # disallowed_special=() keeps user text that happens to contain
            # "<|endoftext|>" from raising.
            return len(encoding.encode(text, disallowed_special=()))
        except Exception:
            logger.warning(
                "tiktoken encode failed; using length estimate", exc_info=True
            )

    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


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
