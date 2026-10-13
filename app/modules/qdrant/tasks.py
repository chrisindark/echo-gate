import logging

from fastapi import BackgroundTasks

from app.core.dependencies import DependencyContainer
from app.core.helpers import dispatch_task
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


def execute_delete_expired_vectors_job() -> None:
    try:
        llm_cache_service = DependencyContainer.get_llm_cache_collection_service()
        llm_cache_service.delete_expired()
    except Exception:
        logger.exception("Failed to run delete_expired_vectors job")


@celery_app.task(name="delete_expired_vectors")
def delete_expired_vectors():
    execute_delete_expired_vectors_job()


def dispatch_delete_expired_vectors(
    background_tasks: BackgroundTasks | None = None,
) -> tuple[str, bool]:
    """
    Dispatch delete_expired_vectors to Celery if online, or fallback to background_tasks.
    """
    return dispatch_task(
        delete_expired_vectors,
        execute_delete_expired_vectors_job,
        background_tasks=background_tasks,
        task_name="delete_expired_vectors",
        task_id_prefix="qdrant-bg-",
    )
