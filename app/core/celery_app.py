import os

from celery import Celery
from celery.schedules import crontab

# Celery uses the REDIS_URL from the environment for both broker and result backend
redis_url = os.getenv("CELERY_REDIS_URL", "redis://localhost:6379/1")

celery_app = Celery(
    "echo_gate_tasks",
    broker=redis_url,
    backend=redis_url,
    include=["app.modules.llm_usage.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Configure periodic tasks (Celery Beat)
    beat_schedule={
        "process-uncalculated-costs-every-5-minutes": {
            "task": "app.modules.llm_usage.tasks.process_uncalculated_costs",
            "schedule": crontab(minute="*/5"),  # Runs every 5 minutes
        },
    },
)
