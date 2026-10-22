import asyncio
import logging
import os
from pathlib import Path

from celery import Celery
from celery.signals import setup_logging, worker_process_init, worker_process_shutdown
from dotenv import load_dotenv

from app.core.config import config
from app.core.dependencies import DependencyContainer
from app.core.logger import setup_logger

logger = logging.getLogger(__name__)

# The event loop used by each prefork worker child. The shared async clients
# (Redis, Qdrant, httpx) are bound to the loop on which the DependencyContainer
# was initialized, so task bodies must run on this same loop.
_worker_loop: asyncio.AbstractEventLoop | None = None


def get_worker_loop() -> asyncio.AbstractEventLoop | None:
    return _worker_loop


def run_async(coro):
    """
    Run a coroutine on the worker-owned event loop.

    Celery task bodies are synchronous. Using ``asyncio.run`` would create a new
    loop per invocation, causing the container's loop-bound async clients to be
    reused across loops and raising ``... attached to a different loop``.
    """
    loop = _worker_loop
    if loop is None or loop.is_closed():
        return asyncio.run(coro)
    return loop.run_until_complete(coro)


# Initialize logger
@setup_logging.connect
def config_loggers(*args, **kwargs):
    setup_logger()


@worker_process_init.connect
def init_worker(**kwargs):
    global _worker_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _worker_loop = loop
    loop.run_until_complete(DependencyContainer.initialize())
    logger.info("DependencyContainer initialized in Celery worker.")


@worker_process_shutdown.connect
def shutdown_worker(**kwargs):
    global _worker_loop
    loop = _worker_loop
    if loop is not None and not loop.is_closed():
        loop.run_until_complete(DependencyContainer.shutdown())
        loop.close()
    _worker_loop = None
    logger.info("DependencyContainer shut down in Celery worker.")


BASE_DIR = Path(__file__).resolve().parent.parent

PYTHON_ENV = os.getenv("PYTHON_ENV", "local")

ENV_FILE = BASE_DIR / f".env.{PYTHON_ENV}"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)
    logger.info(f"Loaded environment file: {ENV_FILE}")
else:
    logger.info(
        f"Environment file not found at {ENV_FILE}; using system environment variables."
    )

load_dotenv()

config.load()

broker_url = config.CELERY_BROKER_URL
result_backend_url = config.CELERY_RESULT_BACKEND

celery_app = Celery(
    "echo_gate_tasks",
    broker=broker_url,
    backend=result_backend_url,
    include=[
        "app.modules.llm_usage.tasks",
        "app.modules.gateway_requests.tasks",
        "app.modules.qdrant.tasks",
    ],
)

beat_schedule = {
    "process-uncalculated-costs-every-5-minutes": {
        "task": "app.modules.llm_usage.tasks.process_uncalculated_costs",
        "schedule": 300.0,  # Runs every 5 minutes
    },
    "evaluate-pending-requests-every-1-minute": {
        "task": "app.modules.gateway_requests.tasks.evaluate_pending_requests",
        "schedule": 60.0,  # Runs every 1 minute
    },
    "delete-expired-vectors-every-5-minutes": {
        "task": "delete_expired_vectors",
        "schedule": 300.0,  # Runs every 5 minutes
    },
}

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=1800,
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=3,
    # Configure periodic tasks (Celery Beat)
    beat_schedule=beat_schedule,
)

from celery.signals import worker_process_init  # noqa: E402


@worker_process_init.connect
def on_worker_process_init(**kwargs):
    """Ensure child Celery worker processes initialize an isolated database connection pool without dropping parent connections."""
    from app.core.database import engine

    engine.dispose(close=False)
