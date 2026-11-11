import asyncio
from unittest.mock import AsyncMock, patch

import app.worker.celery_app as celery_module


def test_get_worker_loop():
    celery_module._worker_loop = None
    assert celery_module.get_worker_loop() is None

    loop = asyncio.new_event_loop()
    celery_module._worker_loop = loop
    assert celery_module.get_worker_loop() == loop
    loop.close()
    celery_module._worker_loop = None


def test_run_async_when_loop_is_none():
    celery_module._worker_loop = None

    async def simple_coro():
        return "result_no_loop"

    assert celery_module.run_async(simple_coro()) == "result_no_loop"


def test_run_async_when_loop_active():
    loop = asyncio.new_event_loop()
    celery_module._worker_loop = loop

    async def simple_coro():
        return "result_with_loop"

    res = celery_module.run_async(simple_coro())
    assert res == "result_with_loop"

    loop.close()
    celery_module._worker_loop = None


def test_config_loggers():
    with patch("app.worker.celery_app.setup_logger") as mock_setup:
        celery_module.config_loggers()
        mock_setup.assert_called_once()


def test_init_and_shutdown_worker():
    with patch(
        "app.core.dependencies.DependencyContainer.initialize", new_callable=AsyncMock
    ) as mock_init:
        celery_module.init_worker()
        assert celery_module._worker_loop is not None
        mock_init.assert_awaited_once()

    with patch(
        "app.core.dependencies.DependencyContainer.shutdown", new_callable=AsyncMock
    ) as mock_shutdown:
        celery_module.shutdown_worker()
        assert celery_module._worker_loop is None
        mock_shutdown.assert_awaited_once()


def test_on_worker_process_init():
    with patch("app.core.database.engine.dispose") as mock_dispose:
        celery_module.on_worker_process_init()
        mock_dispose.assert_called_once_with(close=False)
