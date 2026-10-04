import copy
import functools
import inspect
import logging
import os
import time
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from typing import Any, TypeVar, overload

# Context variable for storing the correlation ID
correlation_id_ctx_var: ContextVar[str] = ContextVar("correlation_id", default="-")


class ColoredFormatter(logging.Formatter):
    """
    A custom formatter that adds colors to log levels and includes the correlation ID.
    """

    def __init__(self, fmt: str | None = None, datefmt=None, style="%"):
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)
        # ANSI escape sequences for colors
        self.colors = {
            logging.DEBUG: "\033[36m",  # Cyan
            logging.INFO: "\033[32m",  # Green
            logging.WARNING: "\033[33m",  # Yellow
            logging.ERROR: "\033[31m",  # Red
            logging.CRITICAL: "\033[1;31m",  # Bold Red
        }
        self.reset = "\033[0m"

    def format(self, record):
        record_copy = copy.copy(record)
        # Add correlation ID to the record
        record_copy.correlation_id = correlation_id_ctx_var.get()
        # Apply color based on the log level
        level_color = self.colors.get(record_copy.levelno, self.reset)

        # Format the rest
        msg = super().format(record_copy)
        return f"{level_color}{msg}{self.reset}"


def setup_logger():
    """
    Configures the root logger with the colored formatter and correlation ID.
    """
    logger = logging.getLogger()

    # Remove existing handlers if any
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    handler = logging.StreamHandler()

    # Define the log format
    fmt = "%(asctime)s %(levelname)s [%(correlation_id)s] - %(name)s - %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = ColoredFormatter(fmt=fmt, datefmt=datefmt, style="%")

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if os.getenv("PYTHON_ENV", "local") == "local":
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    # Update Uvicorn loggers if they exist to use our formatter
    for uvicorn_logger_name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.handlers = [handler]
        # Prevent uvicorn from duplicating logs to the root logger
        uvicorn_logger.propagate = False

    # Disable default uvicorn access logs to prevent duplicates
    logging.getLogger("uvicorn.access").disabled = True


# Type variables for decorator
P = TypeVar("P")
R = TypeVar("R")


@overload
def log_latency(
    action_name: str | None = None,
) -> Callable[
    [Callable[P, Coroutine[Any, Any, R]]], Callable[P, Coroutine[Any, Any, R]]
]: ...


@overload
def log_latency(
    action_name: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def log_latency(
    action_name: str | None = None,
) -> Callable[[Callable[P, Any]], Callable[P, Any]]:
    """
    A decorator to log the latency of synchronous and asynchronous functions.
    """

    def decorator(func):
        logger = logging.getLogger(func.__module__)

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                name = action_name or func.__qualname__
                start_time = time.perf_counter()
                try:
                    return await func(*args, **kwargs)
                finally:
                    end_time = time.perf_counter()
                    latency_ms = int((end_time - start_time) * 1000)
                    logger.info(f"{name} took {latency_ms} ms")

            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                name = action_name or func.__qualname__
                start_time = time.perf_counter()
                try:
                    return func(*args, **kwargs)
                finally:
                    end_time = time.perf_counter()
                    latency_ms = int((end_time - start_time) * 1000)
                    logger.info(f"{name} took {latency_ms} ms")

            return sync_wrapper

    return decorator
