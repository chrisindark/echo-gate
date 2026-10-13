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


# Initialize logger
@setup_logging.connect
def config_loggers(*args, **kwargs):
    setup_logger()


@worker_process_init.connect
def init_worker(**kwargs):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(DependencyContainer.initialize())
    logger.info("DependencyContainer initialized in Celery worker.")


@worker_process_shutdown.connect
def shutdown_worker(**kwargs):
    loop = asyncio.get_event_loop()
    if loop and not loop.is_closed():
        loop.run_until_complete(DependencyContainer.shutdown())
        loop.close()
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
        "schedule": 30.0,  # Runs every 5 minutes (was 30s)
    },
    "evaluate-pending-requests-every-1-minute": {
        "task": "app.modules.gateway_requests.tasks.evaluate_pending_requests",
        "schedule": 30.0,  # Runs every 1 minute
    },
    "delete-expired-vectors-every-5-minutes": {
        "task": "delete_expired_vectors",
        "schedule": 30.0,  # Runs every 5 minutes (was 30s)
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
