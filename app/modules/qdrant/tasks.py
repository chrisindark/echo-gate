import logging

from app.core.celery_app import celery_app
from app.core.dependencies import DependencyContainer

logger = logging.getLogger(__name__)


@celery_app.task(name="delete_expired_vectors")
def delete_expired_vectors():
    try:
        qdrant_service = DependencyContainer.get_qdrant_service()
        qdrant_service.delete_expired()
    except Exception:
        logger.exception("Failed to run delete_expired_vectors task")
