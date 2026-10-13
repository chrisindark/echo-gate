import asyncio
import datetime
import json
import logging
import unittest
import uuid

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.logger import (
    ColoredFormatter,
    JSONFormatter,
    correlation_id_ctx_var,
    get_correlation_id,
    log_latency,
    set_correlation_id,
    setup_logger,
)
from app.core.middlewares import CorrelationIdMiddleware


class TestJSONFormatter(unittest.TestCase):
    def setUp(self):
        correlation_id_ctx_var.set("-")

    def test_json_formatter_standard_fields(self):
        formatter = JSONFormatter(service_name="test-service", environment="testing")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=20,
            msg="Hello %s",
            args=("World",),
            exc_info=None,
        )

        formatted = formatter.format(record)
        data = json.loads(formatted)

        self.assertEqual(data["level"], "INFO")
        self.assertEqual(data["logger"], "test.logger")
        self.assertEqual(data["message"], "Hello World")
        self.assertEqual(data["service"], "test-service")
        self.assertEqual(data["environment"], "testing")
        self.assertIsNone(data["correlation_id"])
        self.assertIn("timestamp", data)

    def test_json_formatter_with_correlation_id(self):
        test_cid = "corr-12345-abcde"
        set_correlation_id(test_cid)

        formatter = JSONFormatter(colorize=False)
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=30,
            msg="Request received",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        data = json.loads(formatted)

        self.assertEqual(data["correlation_id"], test_cid)
        self.assertEqual(get_correlation_id(), test_cid)

    def test_json_formatter_with_extra_fields(self):
        formatter = JSONFormatter(colorize=False)
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=40,
            msg="User action",
            args=(),
            exc_info=None,
        )
        record.user_id = "user_42"
        record.http_status = 200
        record.duration_ms = 12.5

        formatted = formatter.format(record)
        data = json.loads(formatted)

        self.assertEqual(data["user_id"], "user_42")
        self.assertEqual(data["http_status"], 200)
        self.assertEqual(data["duration_ms"], 12.5)

    def test_json_formatter_safe_serialization_of_complex_types(self):
        formatter = JSONFormatter(colorize=False)
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=50,
            msg="Complex data",
            args=(),
            exc_info=None,
        )
        test_uuid = uuid.uuid4()
        test_time = datetime.datetime.now(datetime.UTC)
        record.unique_id = test_uuid
        record.timestamp_field = test_time
        record.tags = {"tag1", "tag2"}
        record.raw_bytes = b"binary-payload"

        formatted = formatter.format(record)
        data = json.loads(formatted)

        self.assertEqual(data["unique_id"], str(test_uuid))
        self.assertEqual(data["timestamp_field"], test_time.isoformat())
        self.assertIsInstance(data["tags"], list)
        self.assertEqual(data["raw_bytes"], "binary-payload")

    def test_json_formatter_exception_block(self):
        formatter = JSONFormatter(colorize=False)
        try:
            raise ValueError("Invalid configuration provided")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=60,
            msg="Operation failed",
            args=(),
            exc_info=exc_info,
        )

        formatted = formatter.format(record)
        data = json.loads(formatted)

        self.assertIn("exception", data)
        self.assertEqual(data["exception"]["type"], "ValueError")
        self.assertEqual(data["exception"]["message"], "Invalid configuration provided")
        self.assertIn("traceback", data["exception"])

    def test_json_formatter_colorize_true(self):
        formatter = JSONFormatter(colorize=True)
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=70,
            msg="Colorized info",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        # Should be wrapped in green (\033[32m) and reset (\033[0m)
        self.assertTrue(formatted.startswith("\033[32m"))
        self.assertTrue(formatted.endswith("\033[0m"))

        # Inner content should be valid JSON
        inner_json = formatted.removeprefix("\033[32m").removesuffix("\033[0m")
        data = json.loads(inner_json)
        self.assertEqual(data["message"], "Colorized info")

    def test_json_formatter_colorize_false(self):
        formatter = JSONFormatter(colorize=False)
        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=80,
            msg="Uncolored error",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        self.assertFalse(formatted.startswith("\033["))
        data = json.loads(formatted)
        self.assertEqual(data["message"], "Uncolored error")


class TestSetupLogger(unittest.TestCase):
    def test_setup_logger_json(self):
        logger = setup_logger(json_format=True, service_name="echo-service")
        self.assertIsInstance(logger.handlers[0].formatter, JSONFormatter)

    def test_setup_logger_colored(self):
        logger = setup_logger(json_format=False)
        self.assertIsInstance(logger.handlers[0].formatter, ColoredFormatter)


class TestLogLatencyDecorator(unittest.TestCase):
    def test_sync_latency_logging(self):
        logs = []

        class MemoryHandler(logging.Handler):
            def emit(self, record):
                logs.append(record)

        handler = MemoryHandler()
        test_logger = logging.getLogger(__name__)
        test_logger.setLevel(logging.INFO)
        test_logger.addHandler(handler)

        @log_latency(action_name="test_sync_op")
        def sync_op():
            return 42

        result = sync_op()
        self.assertEqual(result, 42)
        self.assertTrue(any(getattr(r, "action", None) == "test_sync_op" for r in logs))

    def test_async_latency_logging(self):
        logs = []

        class MemoryHandler(logging.Handler):
            def emit(self, record):
                logs.append(record)

        handler = MemoryHandler()
        test_logger = logging.getLogger(__name__)
        test_logger.setLevel(logging.INFO)
        test_logger.addHandler(handler)

        @log_latency(action_name="test_async_op")
        async def async_op():
            await asyncio.sleep(0.01)
            return "async_done"

        result = asyncio.run(async_op())
        self.assertEqual(result, "async_done")
        self.assertTrue(
            any(getattr(r, "action", None) == "test_async_op" for r in logs)
        )


class TestCorrelationIdMiddleware(unittest.TestCase):
    def setUp(self):
        correlation_id_ctx_var.set("-")

    def test_middleware_propagates_existing_correlation_id(self):
        app = FastAPI()
        app.add_middleware(CorrelationIdMiddleware)

        captured_ctx_id = None

        @app.get("/test")
        def route():
            nonlocal captured_ctx_id
            captured_ctx_id = get_correlation_id()
            return {"status": "ok", "cid": captured_ctx_id}

        client = TestClient(app)
        custom_id = "test-corr-id-999"
        response = client.get("/test", headers={"x-correlation-id": custom_id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("x-correlation-id"), custom_id)
        # x-request-id should be generated as a valid UUID when omitted
        req_id = response.headers.get("x-request-id")
        self.assertIsNotNone(req_id)
        self.assertEqual(str(uuid.UUID(req_id)), req_id)
        self.assertEqual(captured_ctx_id, custom_id)
        # Verify context is cleaned up after request finishes
        self.assertEqual(get_correlation_id(), "")

    def test_middleware_recognizes_request_id_header(self):
        app = FastAPI()
        app.add_middleware(CorrelationIdMiddleware)

        captured_ctx_id = None

        @app.get("/test-req")
        def route():
            nonlocal captured_ctx_id
            captured_ctx_id = get_correlation_id()
            return {"status": "ok"}

        client = TestClient(app)
        custom_req_id = "req-id-777"
        response = client.get("/test-req", headers={"x-request-id": custom_req_id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("x-correlation-id"), custom_req_id)
        self.assertEqual(response.headers.get("x-request-id"), custom_req_id)
        self.assertEqual(captured_ctx_id, custom_req_id)

    def test_middleware_generates_uuid_when_no_header_provided(self):
        app = FastAPI()
        app.add_middleware(CorrelationIdMiddleware)

        captured_ctx_id = None

        @app.get("/test-gen")
        def route():
            nonlocal captured_ctx_id
            captured_ctx_id = get_correlation_id()
            return {"cid": captured_ctx_id}

        client = TestClient(app)
        response = client.get("/test-gen")

        self.assertEqual(response.status_code, 200)
        gen_cid = response.headers.get("x-correlation-id")
        self.assertIsNotNone(gen_cid)
        # Should be a valid UUID
        parsed_uuid = uuid.UUID(gen_cid)
        self.assertEqual(str(parsed_uuid), gen_cid)
        self.assertEqual(captured_ctx_id, gen_cid)


if __name__ == "__main__":
    unittest.main()
