from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.main import app
from app.modules.llm_usage.llm_usage_model import LlmUsageLog
from app.modules.llm_usage.llm_usage_schema import (
    LlmUsageLogCreate,
    LlmUsageLogResponse,
    LlmUsageLogUpdate,
)
from app.modules.llm_usage.llm_usage_service import LlmUsageService
from app.modules.llm_usage.tasks import (
    dispatch_process_uncalculated_costs,
    execute_process_uncalculated_costs_job,
    process_uncalculated_costs,
)


@pytest.fixture
def mock_db_session():
    session = MagicMock(spec=Session)
    return session


def test_llm_usage_service_init_permutations(mock_db_session):
    # Init with Session instances
    svc1 = LlmUsageService(
        session_factory=mock_db_session, session_factory_read=mock_db_session
    )
    assert svc1._db_session == mock_db_session
    assert svc1._db_session_read == mock_db_session

    # Init with factories
    mock_factory = MagicMock()
    svc2 = LlmUsageService(
        session_factory=mock_factory, session_factory_read=mock_factory
    )
    assert svc2._session_factory == mock_factory
    assert svc2._session_factory_read == mock_factory

    # Init defaults
    svc3 = LlmUsageService()
    assert svc3._session_factory is not None
    assert svc3._session_factory_read is not None


def test_llm_usage_service_session_contexts(mock_db_session):
    # When _db_session is provided
    svc = LlmUsageService(db_session=mock_db_session, db_session_read=mock_db_session)
    with svc._get_session() as s:
        assert s == mock_db_session
    with svc._get_read_session() as s:
        assert s == mock_db_session

    # When factory is provided
    mock_s = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = mock_s
    svc_fact = LlmUsageService(session_factory=factory, session_factory_read=factory)
    with svc_fact._get_session() as s:
        assert s == mock_s
    with svc_fact._get_read_session() as s:
        assert s == mock_s


def test_llm_usage_service_crud(mock_db_session):
    svc = LlmUsageService(db_session=mock_db_session, db_session_read=mock_db_session)

    # 1. create_llm_usage_log
    log_data = LlmUsageLogCreate(
        service_name="openai",
        model="gpt-4o",
        caller_service_name="chat",
        prompt_tokens=10,
        completion_tokens=20,
    )
    created = svc.create_llm_usage_log(log_data)
    assert created.service_name == "openai"
    mock_db_session.add.assert_called_once()
    mock_db_session.commit.assert_called_once()
    mock_db_session.refresh.assert_called_once()

    # create error handling
    mock_db_session.commit.side_effect = RuntimeError("db error")
    with pytest.raises(RuntimeError):
        svc.create_llm_usage_log(log_data)
    mock_db_session.rollback.assert_called()

    mock_db_session.commit.side_effect = None

    # 2. get_llm_usage_log
    mock_query = mock_db_session.query.return_value
    mock_filter = mock_query.filter.return_value
    mock_filter.first.return_value = created
    res = svc.get_llm_usage_log(1)
    assert res == created

    # 3. get_llm_usage_logs with filters
    now = datetime.now(UTC)
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.offset.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.all.return_value = [created]

    logs = svc.get_llm_usage_logs(start_time=now, end_time=now, skip=0, limit=10)
    assert len(logs) == 1

    # query error handling
    mock_db_session.query.side_effect = RuntimeError("query failed")
    with pytest.raises(RuntimeError):
        svc.get_llm_usage_logs()
    mock_db_session.query.side_effect = None

    # 4. update_llm_usage_log
    # Not found
    mock_db_session.query.return_value.filter.return_value.first.return_value = None
    update_data = LlmUsageLogUpdate(prompt_tokens=15)
    assert svc.update_llm_usage_log(999, update_data) is None

    # Found & success
    existing_log = LlmUsageLog(
        service_name="openai", model="gpt-4o", caller_service_name="chat"
    )
    mock_db_session.query.return_value.filter.return_value.first.return_value = (
        existing_log
    )
    updated = svc.update_llm_usage_log(1, update_data)
    assert updated.prompt_tokens == 15

    # Update error
    mock_db_session.commit.side_effect = RuntimeError("update fail")
    with pytest.raises(RuntimeError):
        svc.update_llm_usage_log(1, update_data)

    mock_db_session.commit.side_effect = None

    # 5. delete_llm_usage_log
    # Not found
    mock_db_session.query.return_value.filter.return_value.first.return_value = None
    assert svc.delete_llm_usage_log(999) is False

    # Found & success
    mock_db_session.query.return_value.filter.return_value.first.return_value = (
        existing_log
    )
    assert svc.delete_llm_usage_log(1) is True
    mock_db_session.delete.assert_called_with(existing_log)

    # Delete error
    mock_db_session.commit.side_effect = RuntimeError("del fail")
    with pytest.raises(RuntimeError):
        svc.delete_llm_usage_log(1)

    mock_db_session.commit.side_effect = None


def test_llm_usage_service_costs_and_backfill(mock_db_session):
    svc = LlmUsageService(db_session=mock_db_session, db_session_read=mock_db_session)

    # get_pending_cost_calculations
    log_item = LlmUsageLog(
        service_name="openai", model="gpt-4o", caller_service_name="chat"
    )
    mock_query = mock_db_session.query.return_value
    mock_filter = mock_query.filter.return_value
    mock_order = mock_filter.order_by.return_value
    mock_order.limit.return_value.all.return_value = [log_item]

    pending = svc.get_pending_cost_calculations(limit=10)
    assert len(pending) == 1

    # Exception in get_pending_cost_calculations
    mock_db_session.query.side_effect = RuntimeError("fail")
    with pytest.raises(RuntimeError):
        svc.get_pending_cost_calculations()
    mock_db_session.query.side_effect = None

    # bulk_update_costs
    updates = [{"id": 1, "cost": Decimal("0.05"), "cost_calculated": True}]
    assert svc.bulk_update_costs(updates) is True
    mock_db_session.bulk_update_mappings.assert_called_with(LlmUsageLog, updates)

    mock_db_session.commit.side_effect = RuntimeError("bulk fail")
    with pytest.raises(RuntimeError):
        svc.bulk_update_costs(updates)
    mock_db_session.commit.side_effect = None

    # backfill_prompt_hashes
    row1 = MagicMock()
    row1.id = 1
    row1.input_text = "system: You are a helpful assistant.\nuser: Hello"

    row2 = MagicMock()
    row2.id = 2
    row2.input_text = "Just a raw string with no system prompt"

    # First iteration returns [row1, row2], second returns []
    mock_db_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.side_effect = [
        [row1, row2],
        [],
    ]
    with patch(
        "app.modules.llm_usage.llm_usage_service.system_prompt_from_prompt_text"
    ) as mock_sys:
        mock_sys.side_effect = ["You are a helpful assistant.", None]
        scanned, updated, skipped = svc.backfill_prompt_hashes(batch_size=10)
        assert scanned == 2
        assert updated == 1
        assert skipped == 1


def test_llm_usage_controller_endpoints():
    mock_svc = MagicMock(spec=LlmUsageService)

    sample_log = LlmUsageLogResponse(
        id=1,
        service_name="openai",
        model="gpt-4o",
        caller_service_name="test",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    mock_svc.get_llm_usage_logs.return_value = [sample_log]
    mock_svc.get_llm_usage_log.return_value = sample_log
    mock_svc.create_llm_usage_log.return_value = sample_log
    mock_svc.update_llm_usage_log.return_value = sample_log
    mock_svc.delete_llm_usage_log.return_value = True

    mock_db = MagicMock()
    app.dependency_overrides[get_db] = lambda: mock_db

    with patch(
        "app.modules.llm_usage.llm_usage_controller.LlmUsageService",
        return_value=mock_svc,
    ):
        client = TestClient(app)

        # GET /
        resp = client.get("/api/v1/llm-usage/?skip=0&limit=10")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # GET /{id}
        resp = client.get("/api/v1/llm-usage/1")
        assert resp.status_code == 200

        mock_svc.get_llm_usage_log.return_value = None
        resp_404 = client.get("/api/v1/llm-usage/999")
        assert resp_404.status_code == 404

        # POST /
        mock_svc.create_llm_usage_log.return_value = sample_log
        resp = client.post(
            "/api/v1/llm-usage/",
            json={
                "service_name": "openai",
                "model": "gpt-4o",
                "caller_service_name": "test",
            },
        )
        assert resp.status_code == 201

        mock_svc.create_llm_usage_log.side_effect = Exception("create error")
        resp_err = client.post(
            "/api/v1/llm-usage/",
            json={
                "service_name": "openai",
                "model": "gpt-4o",
                "caller_service_name": "test",
            },
        )
        assert resp_err.status_code == 500

        # PUT /{id}
        mock_svc.update_llm_usage_log.side_effect = None
        mock_svc.update_llm_usage_log.return_value = sample_log
        resp = client.put("/api/v1/llm-usage/1", json={"prompt_tokens": 50})
        assert resp.status_code == 200

        mock_svc.update_llm_usage_log.return_value = None
        resp_404 = client.put("/api/v1/llm-usage/999", json={"prompt_tokens": 50})
        assert resp_404.status_code == 404

        mock_svc.update_llm_usage_log.side_effect = Exception("update error")
        resp_err = client.put("/api/v1/llm-usage/1", json={"prompt_tokens": 50})
        assert resp_err.status_code == 500

        # DELETE /{id}
        mock_svc.delete_llm_usage_log.side_effect = None
        mock_svc.delete_llm_usage_log.return_value = True
        resp = client.delete("/api/v1/llm-usage/1")
        assert resp.status_code == 200

        mock_svc.delete_llm_usage_log.return_value = False
        resp_404 = client.delete("/api/v1/llm-usage/999")
        assert resp_404.status_code == 404

        mock_svc.delete_llm_usage_log.side_effect = Exception("del error")
        resp_err = client.delete("/api/v1/llm-usage/1")
        assert resp_err.status_code == 500

        # POST /calculate-costs
        with patch(
            "app.modules.llm_usage.llm_usage_controller.dispatch_process_uncalculated_costs",
            return_value=("task-123", True),
        ):
            resp = client.post("/api/v1/llm-usage/calculate-costs")
            assert resp.status_code == 200
            assert "Celery" in resp.json()["response"]["message"]

        with patch(
            "app.modules.llm_usage.llm_usage_controller.dispatch_process_uncalculated_costs",
            return_value=("task-123", False),
        ):
            resp = client.post("/api/v1/llm-usage/calculate-costs")
            assert resp.status_code == 200
            assert "background worker" in resp.json()["response"]["message"]

    app.dependency_overrides.pop(get_db, None)


def test_llm_usage_tasks():
    # 1. execute_process_uncalculated_costs_job empty
    mock_db = MagicMock()
    with patch("app.modules.llm_usage.tasks.task_db_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = mock_db
        with patch("app.modules.llm_usage.tasks.LlmUsageService") as mock_svc_cls:
            mock_svc = mock_svc_cls.return_value
            mock_svc.get_pending_cost_calculations.return_value = []
            res = execute_process_uncalculated_costs_job(
                task_id="test-task", db_session=mock_db
            )
            assert res == "No rows to update."

            # with rows to update
            log1 = MagicMock()
            log1.id = 1
            log1.service_name = "openai"
            log1.model = "gpt-4o"
            log1.prompt_tokens = 100
            log1.completion_tokens = 200
            mock_svc.get_pending_cost_calculations.return_value = [log1]

            with patch(
                "app.modules.llm_usage.tasks.get_model_prices",
                return_value=(0.000005, 0.000015),
            ):
                res_updated = execute_process_uncalculated_costs_job(
                    task_id="test-task", db_session=mock_db
                )
                assert "Updated 1 rows" in res_updated

            # exception path
            mock_svc.get_pending_cost_calculations.side_effect = RuntimeError(
                "job error"
            )
            with pytest.raises(RuntimeError):
                execute_process_uncalculated_costs_job(
                    task_id="test-task", db_session=mock_db
                )

    # 2. process_uncalculated_costs
    process_uncalculated_costs.request.id = "celery-id-123"
    with patch(
        "app.modules.llm_usage.tasks.execute_process_uncalculated_costs_job",
        return_value="done",
    ) as mock_job:
        out = process_uncalculated_costs.run()
        assert out == "done"
        mock_job.assert_called_once_with(task_id="celery-id-123")

    # 3. dispatch_process_uncalculated_costs
    with patch(
        "app.modules.llm_usage.tasks.dispatch_task", return_value=("task-id", True)
    ):
        tid, celery_flag = dispatch_process_uncalculated_costs()
        assert tid == "task-id"
        assert celery_flag is True


def test_llm_usage_service_more_coverage(mock_db_session):
    svc = LlmUsageService(db_session=mock_db_session, db_session_read=mock_db_session)

    # 1. get_llm_usage_logs with start_time and end_time
    mock_query = mock_db_session.query.return_value
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.offset.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.all.return_value = []

    now = datetime.now(UTC)
    res = svc.get_llm_usage_logs(start_time=now, end_time=now)
    assert res == []
    svc.get_llm_usage_logs(start_time=now, end_time=None)
    svc.get_llm_usage_logs(start_time=None, end_time=now)

    # 2. backfill_prompt_hashes with unparseable rows and loop termination
    row_unparseable = MagicMock()
    row_unparseable.id = 101
    row_unparseable.input_text = "raw prompt without model separator"

    # First iteration returns unparseable row, second iteration returns empty list
    mock_query.all.side_effect = [[row_unparseable], []]
    with patch(
        "app.modules.llm_usage.llm_usage_service.system_prompt_from_prompt_text",
        return_value=None,
    ):
        scanned, updated, unparseable_cnt = svc.backfill_prompt_hashes(batch_size=1)
        assert scanned == 1
        assert updated == 0
        assert unparseable_cnt == 1

    # 3. Fallback context managers
    with patch("app.modules.llm_usage.llm_usage_service.SessionLocal") as mock_sl:
        mock_s = MagicMock()
        mock_sl.return_value.__enter__.return_value = mock_s
        svc_empty = LlmUsageService()
        svc_empty._session_factory = None
        svc_empty._session_factory_read = None
        with svc_empty._get_session() as s:
            assert s == mock_s
        with svc_empty._get_read_session() as s:
            assert s == mock_s
