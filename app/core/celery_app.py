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

if not ENV_FILE.exists():
    raise RuntimeError(f"Environment file not found: {ENV_FILE}")

load_dotenv(ENV_FILE)

logger.info(f"Loaded environment: {PYTHON_ENV}")

load_dotenv()

config.load()

# Celery uses the REDIS_URL from the environment for both broker and result backend
redis_url = config.CELERY_REDIS_URL

celery_app = Celery(
    "echo_gate_tasks",
    broker=redis_url,
    backend=redis_url,
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
    # Configure periodic tasks (Celery Beat)
    beat_schedule=beat_schedule,
)
