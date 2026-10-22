import copy
import datetime
import functools
import inspect
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, TypeVar

# Context variable for storing the correlation ID across async tasks
correlation_id_ctx_var: ContextVar[str] = ContextVar("correlation_id", default="-")


def get_correlation_id(default: str = "") -> str:
    """
    Retrieve the active correlation ID from the context variable.
    Returns `default` if not set or set to placeholder '-'.
    """
    cid = correlation_id_ctx_var.get()
    if not cid or cid == "-":
        return default
    return cid


def set_correlation_id(correlation_id: str) -> None:
    """
    Set the active correlation ID in the context variable.
    """
    correlation_id_ctx_var.set(correlation_id)


def _json_serializer(obj: Any) -> Any:
    """
    Safe serializer for non-primitive objects to prevent logging crashes.
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, BaseException):
        return str(obj)
    return str(obj)


_RESERVED_RECORD_ATTRS: set[str] = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
    "correlation_id",
}


class JSONFormatter(logging.Formatter):
    """
    Structured JSON log formatter.
    Outputs one JSON object per line with standard fields, correlation ID,
    service metadata, custom extra properties, and formatted exceptions.
    Optionally colorizes the JSON output when run interactively in a local terminal.
    """

    def __init__(
        self,
        service_name: str | None = None,
        environment: str | None = None,
        colorize: bool | None = None,
        datefmt: str | None = None,
    ):
        super().__init__(datefmt=datefmt)
        self.service_name = service_name or os.getenv("SERVICE_NAME", "")
        self.environment = environment or os.getenv(
            "PYTHON_ENV", os.getenv("ENVIRONMENT", "local")
        )

        if colorize is None:
            colorize_env = os.getenv("COLORIZE_JSON", "").lower().strip()
            if colorize_env in ("true", "1", "yes"):
                self.colorize = True
            elif colorize_env in ("false", "0", "no"):
                self.colorize = False
            else:
                self.colorize = (
                    hasattr(sys.stdout, "isatty")
                    and sys.stdout.isatty()
                    and self.environment == "local"
                )
        else:
            self.colorize = colorize

        self.colors = {
            logging.DEBUG: "\033[36m",  # Cyan
            logging.INFO: "\033[32m",  # Green
            logging.WARNING: "\033[33m",  # Yellow
            logging.ERROR: "\033[31m",  # Red
            logging.CRITICAL: "\033[1;31m",  # Bold Red
        }
        self.reset = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        now = datetime.datetime.now(datetime.UTC)
        timestamp_str = now.isoformat()

        cid = correlation_id_ctx_var.get()
        correlation_id = cid if cid and cid != "-" else None

        log_data: dict[str, Any] = {
            "timestamp": timestamp_str,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id,
            "service": self.service_name,
            "environment": self.environment,
        }

        # Include extra attributes passed via extra={...}
        extra_fields: dict[str, Any] = {}
        for key, val in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS and not key.startswith("_"):
                if key in log_data:
                    extra_fields[key] = val
                else:
                    log_data[key] = val

        if extra_fields:
            log_data["extra"] = extra_fields

        # Format exception information if present
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            exc_type = (
                record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
            )
            exc_msg = str(record.exc_info[1]) if record.exc_info[1] else ""
            log_data["exception"] = {
                "type": exc_type,
                "message": exc_msg,
                "traceback": record.exc_text,
            }

        if record.stack_info:
            log_data["stack_info"] = self.formatStack(record.stack_info)

        try:
            raw_json = json.dumps(
                log_data, default=_json_serializer, ensure_ascii=False
            )
        except (TypeError, ValueError) as err:
            fallback = {
                "timestamp": timestamp_str,
                "level": "ERROR",
                "logger": record.name,
                "message": f"Log serialization error: {err}",
                "raw_message": str(record.msg),
                "correlation_id": correlation_id,
            }
            raw_json = json.dumps(
                fallback, default=_json_serializer, ensure_ascii=False
            )

        if self.colorize:
            color = self.colors.get(record.levelno, self.reset)
            return f"{color}{raw_json}{self.reset}"
        return raw_json


class ColoredFormatter(logging.Formatter):
    """
    A custom formatter that adds colors to log levels and includes the correlation ID.
    Used for local development when LOG_FORMAT=console or LOG_FORMAT=text.
    """

    def __init__(self, fmt: str | None = None, datefmt=None, style="%"):
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)
        self.colors = {
            logging.DEBUG: "\033[36m",  # Cyan
            logging.INFO: "\033[32m",  # Green
            logging.WARNING: "\033[33m",  # Yellow
            logging.ERROR: "\033[31m",  # Red
            logging.CRITICAL: "\033[1;31m",  # Bold Red
        }
        self.reset = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        record_copy = copy.copy(record)
        cid = correlation_id_ctx_var.get()
        record_copy.correlation_id = cid if cid else "-"
        level_color = self.colors.get(record_copy.levelno, self.reset)
        msg = super().format(record_copy)
        return f"{level_color}{msg}{self.reset}"


def setup_logger(
    log_level: str | int | None = None,
    json_format: bool | None = None,
    service_name: str | None = None,
    environment: str | None = None,
    colorize: bool | None = None,
) -> logging.Logger:
    """
    Configures the root logger and framework loggers (Uvicorn, Celery).
    Defaults to structured JSON logging out of the box, or colored text
    when LOG_FORMAT=console or LOG_FORMAT=text.
    """
    logger = logging.getLogger()

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)

    if json_format is None:
        log_format_env = os.getenv("LOG_FORMAT", "json").lower().strip()
        json_format = log_format_env not in ("console", "text", "colored")

    if json_format:
        formatter: logging.Formatter = JSONFormatter(
            service_name=service_name,
            environment=environment,
            colorize=colorize,
        )
    else:
        fmt = "%(asctime)s %(levelname)s [%(correlation_id)s] - %(name)s - %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"
        formatter = ColoredFormatter(fmt=fmt, datefmt=datefmt, style="%")

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if log_level is None:
        env_level = os.getenv("LOG_LEVEL")
        if env_level:
            log_level = getattr(logging, env_level.upper(), logging.INFO)
        elif os.getenv("PYTHON_ENV", "local") == "local":
            log_level = logging.DEBUG
        else:
            log_level = logging.INFO

    logger.setLevel(log_level)

    # Configure Uvicorn loggers to use the shared structured handler
    for uvicorn_logger_name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.handlers = [handler]
        uvicorn_logger.propagate = False

    # Disable default uvicorn access logs to prevent duplicates with CorrelationIdMiddleware
    logging.getLogger("uvicorn.access").disabled = True

    return logger


F = TypeVar("F", bound=Callable[..., Any])


def log_latency(
    action_name: str | None = None,
) -> Callable[[F], F]:
    """
    A decorator to log the latency of synchronous and asynchronous functions.
    Attaches structured latency_ms and action metadata to the log record.
    """

    def decorator(func: F) -> F:
        func_logger = logging.getLogger(func.__module__)

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
                    func_logger.info(
                        f"{name} took {latency_ms} ms",
                        extra={"action": name, "latency_ms": latency_ms},
                    )

            return async_wrapper  # type: ignore
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
                    func_logger.info(
                        f"{name} took {latency_ms} ms",
                        extra={"action": name, "latency_ms": latency_ms},
                    )

            return sync_wrapper  # type: ignore

    return decorator
