"""Celery tasks for core infrastructure.

Currently only the event bus delivery task: dispatch prefers Celery when the
broker is online (see ``app.core.events._dispatch``), so a webhook POST happens
in a worker process instead of on the gateway's event loop. The task is kept out
of ``app.core.events`` on purpose — that module must stay importable without
``app.worker.celery_app`` and its dependency container.
"""

import logging
from typing import Any

from app.core.events import deliver
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.core.events.deliver_event")
def deliver_event(event: dict[str, Any]) -> None:
    """Deliver one typed event to its configured sink. Fail-open."""
    deliver(event)
