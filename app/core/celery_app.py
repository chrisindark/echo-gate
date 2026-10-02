from celery import Celery

from app.core.config import config
from app.core.logger import setup_logger

# Initialize the custom colored logger
setup_logger()

config.load()

# Celery uses the REDIS_URL from the environment for both broker and result backend
redis_url = config.CELERY_REDIS_URL


celery_app = Celery(
    "echo_gate_tasks",
    broker=redis_url,
    backend=redis_url,
    include=["app.modules.llm_usage.tasks"],
)

beat_schedule = {
    "process-uncalculated-costs-every-5-minutes": {
        "task": "app.modules.llm_usage.tasks.process_uncalculated_costs",
        # "schedule": crontab(minute="*/5"),  # Runs every 5 minutes
        "schedule": 30.0,  # Runs every 30 seconds
    }
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
