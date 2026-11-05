"""Event bus / webhook sink seam tests.

Contract pinned here:

* The seam is inert until an event type has a destination: no dispatch work,
  no payload building, no broker probe.
* Emission is fail-open — a sink or webhook that raises never propagates.
* Payloads never carry credentials, and never prompt/response text.
* Delivery is off-path: an event emitted from a sync caller runs on a daemon
  thread, and an event emitted inside a loop runs as a task, so a slow webhook
  cannot delay the caller.
"""

import asyncio
import json
import threading
import time
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from app.core import events
from app.core.config import config


class _CaptureSink:
    def __init__(self, raises: bool = False) -> None:
        self.events: list[dict] = []
        self.raises = raises

    def emit(self, event: dict) -> None:
        if self.raises:
            raise RuntimeError("sink exploded")
        self.events.append(event)


class TestEventBuilding(unittest.TestCase):
    def setUp(self):
        self._saved = {
            name: getattr(config, name)
            for name in (
                "EVENTS_ENABLED",
                "EVENTS_WEBHOOK_CACHE_MISS",
                "EVENTS_WEBHOOK_QUOTA_EXCEEDED",
                "EVENTS_WEBHOOK_PROVIDER_FAILURE",
                "EVENTS_WEBHOOK_AUDIT_FAILED",
            )
        }
        events.set_event_sink(None)
        for name in self._saved:
            setattr(config, name, "" if name != "EVENTS_ENABLED" else True)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(config, name, value)
        events.set_event_sink(None)

    def test_unknown_event_type_is_dropped(self):
        sink = _CaptureSink()
        self.assertIsNone(events.emit_event("nope.not.real", {}, sink=sink))
        self.assertEqual(sink.events, [])

    def test_event_shape(self):
        sink = _CaptureSink()
        event = events.emit_event(
            events.EVENT_CACHE_MISS, {"model": "gpt-4o-mini"}, sink=sink
        )
        assert event is not None
        self.assertEqual(event["type"], events.EVENT_CACHE_MISS)
        self.assertIsInstance(event["ts"], float)
        self.assertEqual(event["payload"], {"model": "gpt-4o-mini"})
        self.assertEqual(sink.events, [event])

    def test_no_destination_means_no_dispatch_work(self):
        with patch.object(events, "_dispatch") as dispatch:
            self.assertIsNone(events.emit_event(events.EVENT_CACHE_MISS, {"a": 1}))
        dispatch.assert_not_called()

    def test_disabled_switch_drops_every_event(self):
        config.EVENTS_ENABLED = False
        config.EVENTS_WEBHOOK_CACHE_MISS = "https://example.test/hook"
        self.assertFalse(events.events_enabled())
        self.assertFalse(events.is_configured(events.EVENT_CACHE_MISS))
        with patch.object(events, "_dispatch") as dispatch:
            self.assertIsNone(events.emit_event(events.EVENT_CACHE_MISS, {}))
        dispatch.assert_not_called()

    def test_configured_type_is_dispatched(self):
        config.EVENTS_WEBHOOK_CACHE_MISS = "https://example.test/hook"
        with patch.object(events, "_dispatch") as dispatch:
            event = events.emit_event(events.EVENT_CACHE_MISS, {"a": 1})
        assert event is not None
        dispatch.assert_called_once_with(event)

    def test_unconfigured_type_is_isolated_from_configured_ones(self):
        config.EVENTS_WEBHOOK_CACHE_MISS = "https://example.test/hook"
        self.assertTrue(events.is_configured(events.EVENT_CACHE_MISS))
        self.assertFalse(events.is_configured(events.EVENT_AUDIT_FAILED))

    def test_injected_sink_bypasses_url_gating(self):
        # A cloud overlay may fan out to destinations that are not in config.
        sink = _CaptureSink()
        events.set_event_sink(sink)
        self.assertTrue(events.is_configured(events.EVENT_AUDIT_FAILED))
        with patch.object(events, "_dispatch") as dispatch:
            event = events.emit_event(events.EVENT_AUDIT_FAILED, {"a": 1})
        assert event is not None
        dispatch.assert_called_once_with(event)
        self.assertEqual(events.default_sink(), sink)

    def test_sink_failure_is_swallowed(self):
        events.set_event_sink(_CaptureSink(raises=True))
        event = events.emit_event(events.EVENT_QUOTA_EXCEEDED, {}, sink=_CaptureSink(raises=True))
        self.assertIsNotNone(event)  # never raises

    def test_delivery_failure_is_swallowed(self):
        events.set_event_sink(_CaptureSink(raises=True))
        events.deliver({"type": events.EVENT_CACHE_MISS, "payload": {}})  # no raise


class TestSanitization(unittest.TestCase):
    def setUp(self):
        self.capture = _CaptureSink()
        events.set_event_sink(self.capture)

    def tearDown(self):
        events.set_event_sink(None)

    def test_credential_keys_are_stripped_recursively(self):
        sink = _CaptureSink()
        event = events.emit_event(
            events.EVENT_PROVIDER_FAILURE,
            {
                "api_key": "sk-secret",
                "provider": "openai",
                "headers": {
                    "Authorization": "Bearer secret",
                    "x-api-key": "sk-secret",
                    "cookie": "session=abc",
                },
                "attempts": [{"password": "hunter2", "status_code": 429}],
                "token_count": 12,
            },
            sink=sink,
        )
        assert event is not None
        payload = event["payload"]
        self.assertNotIn("api_key", payload)
        self.assertNotIn("Authorization", payload["headers"])
        self.assertNotIn("x-api-key", payload["headers"])
        self.assertNotIn("cookie", payload["headers"])
        self.assertNotIn("password", payload["attempts"][0])
        # Metrics that merely mention tokens are not credentials.
        self.assertEqual(payload["token_count"], 12)
        self.assertEqual(payload["attempts"][0]["status_code"], 429)
        self.assertNotIn("sk-secret", json.dumps(payload))

    def test_payloads_never_carry_prompt_text(self):
        # The typed emitters take operational metadata only; there is no prompt
        # or response parameter to pass one by accident.
        events.emit_cache_miss(
            exact_hash="abc",
            tenant_id="t1",
            model="gpt-4o-mini",
            provider="openai",
            intent="general_query",
        )
        payload = self.capture.events[-1]["payload"]
        for forbidden in ("query", "prompt", "input_text", "output_text", "answer"):
            self.assertNotIn(forbidden, payload)


class _FakeHttpResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class TestWebhookSink(unittest.TestCase):
    def _response(self, status: int = 200) -> _FakeHttpResponse:
        return _FakeHttpResponse(status)

    def test_posts_to_the_url_for_its_type(self):
        sink = events.WebhookEventSink(
            {"cache.miss": "https://example.test/miss"}
        )
        with patch("urllib.request.urlopen", return_value=self._response()) as urlopen:
            sink.emit({"type": "cache.miss", "payload": {"a": 1}})
        urlopen.assert_called_once()
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://example.test/miss")
        self.assertEqual(json.loads(req.data)["payload"], {"a": 1})
        self.assertEqual(req.get_header("Content-type"), "application/json")

    def test_unconfigured_type_is_not_posted(self):
        sink = events.WebhookEventSink({"cache.miss": "https://example.test/miss"})
        with patch("urllib.request.urlopen") as urlopen:
            sink.emit({"type": "audit.failed", "payload": {}})
        urlopen.assert_not_called()

    def test_http_error_and_transport_failure_are_ignored(self):
        sink = events.WebhookEventSink({"cache.miss": "https://example.test/miss"})
        with patch("urllib.request.urlopen", return_value=self._response(500)):
            sink.emit({"type": "cache.miss", "payload": {}})  # no raise
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            sink.emit({"type": "cache.miss", "payload": {}})  # no raise

    def test_serializes_non_json_values(self):
        sink = events.WebhookEventSink({"cache.miss": "https://example.test/miss"})
        with patch("urllib.request.urlopen", return_value=self._response()) as urlopen:
            sink.emit({"type": "cache.miss", "payload": {"when": time.time()}})
        self.assertIsInstance(json.loads(urlopen.call_args.args[0].data), dict)


class TestOffPathDelivery(unittest.TestCase):
    def setUp(self):
        self._saved_sink = events._sink
        events.set_event_sink(None)

    def tearDown(self):
        events._sink = self._saved_sink

    def test_sync_caller_delivers_on_a_daemon_thread(self):
        delivered = threading.Event()
        names: list[str] = []

        class _ThreadRecordingSink:
            def emit(self, event):
                names.append(threading.current_thread().name)
                delivered.set()

        events.set_event_sink(_ThreadRecordingSink())
        with patch(
            "app.core.helpers.is_celery_broker_online", return_value=False
        ):
            events._dispatch({"type": "cache.miss", "payload": {}})
            # Called from the main thread with no running loop: must not block,
            # and delivery happens on the daemon thread.
            self.assertTrue(delivered.wait(timeout=5))
        self.assertNotEqual(names, [threading.current_thread().name])
        self.assertTrue(names[0].startswith("event-delivery"))

    def test_async_caller_does_not_block_on_a_slow_sink(self):
        release = threading.Event()

        class _SlowSink:
            def emit(self, event):
                release.wait(timeout=5)

        events.set_event_sink(_SlowSink())

        async def _emit_and_return() -> float:
            start = time.monotonic()
            with patch(
                "app.core.helpers.is_celery_broker_online", return_value=False
            ):
                events._dispatch({"type": "cache.miss", "payload": {}})
            return time.monotonic() - start

        try:
            elapsed = asyncio.run(_emit_and_return())
        finally:
            release.set()
        self.assertLess(elapsed, 1.0)

    def test_celery_dispatch_is_preferred_when_broker_is_online(self):
        task = MagicMock()
        with patch(
            "app.core.helpers.is_celery_broker_online", return_value=True
        ), patch.object(events, "_celery_delivery_task", return_value=task):
            events._dispatch({"type": "cache.miss", "payload": {}})
        task.delay.assert_called_once_with({"type": "cache.miss", "payload": {}})

    def test_celery_failure_falls_back_in_process(self):
        delivered = threading.Event()

        class _Sink:
            def emit(self, event):
                delivered.set()

        task = MagicMock()
        task.delay.side_effect = RuntimeError("broker down")
        events.set_event_sink(_Sink())
        with patch(
            "app.core.helpers.is_celery_broker_online", return_value=True
        ), patch.object(events, "_celery_delivery_task", return_value=task):
            events._dispatch({"type": "cache.miss", "payload": {}})
            self.assertTrue(delivered.wait(timeout=5))

    def test_delivery_task_is_registered_with_celery(self):
        # The task belongs to app.core.tasks; events.py resolves it lazily so
        # registration never depends on import order.
        from app.core.tasks import deliver_event
        from app.worker.celery_app import celery_app

        self.assertEqual(deliver_event.name, "app.core.events.deliver_event")
        self.assertIn("app.core.events.deliver_event", celery_app.tasks)
        resolved = events._celery_delivery_task()
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.name, "app.core.events.deliver_event")


class TestTypedEmitters(unittest.TestCase):
    def setUp(self):
        self.sink = _CaptureSink()
        events.set_event_sink(self.sink)

    def tearDown(self):
        events.set_event_sink(None)

    def test_cache_miss(self):
        events.emit_cache_miss(
            exact_hash="h",
            tenant_id="t1",
            user_id="u1",
            provider="openai",
            model="gpt-4o-mini",
            intent="general_query",
            cache_mode="active",
            latency_ms=42,
        )
        event = self.sink.events[-1]
        self.assertEqual(event["type"], events.EVENT_CACHE_MISS)
        self.assertEqual(event["payload"]["exact_hash"], "h")
        self.assertEqual(event["payload"]["cache_mode"], "active")
        self.assertEqual(event["payload"]["latency_ms"], 42)

    def test_quota_exceeded(self):
        events.emit_quota_exceeded(
            reason="rate_limit",
            tenant_id="t1",
            user_id="u1",
            provider="openai",
            model="gpt-4o-mini",
            limit=10,
            used=11,
            retry_after_seconds=30,
        )
        event = self.sink.events[-1]
        self.assertEqual(event["type"], events.EVENT_QUOTA_EXCEEDED)
        self.assertEqual(event["payload"]["reason"], "rate_limit")
        self.assertEqual(event["payload"]["limit"], 10)
        self.assertEqual(event["payload"]["retry_after_seconds"], 30)

    def test_provider_failure(self):
        events.emit_provider_failure(
            provider="groq",
            model="openai/gpt-oss-20b",
            error_code="provider_unavailable",
            status_code=503,
            detail="upstream 503",
            tenant_id="t1",
            final=True,
        )
        event = self.sink.events[-1]
        self.assertEqual(event["type"], events.EVENT_PROVIDER_FAILURE)
        self.assertEqual(event["payload"]["provider"], "groq")
        self.assertEqual(event["payload"]["error_code"], "provider_unavailable")
        self.assertTrue(event["payload"]["final"])

    def test_audit_failed(self):
        events.emit_audit_failed(
            exact_hash="h",
            tenant_id="t1",
            model="gpt-4o-mini",
            match_type="qdrant_semantic_path",
            rerank_score=0.81,
            vector_similarity=0.93,
            evicted=True,
        )
        event = self.sink.events[-1]
        self.assertEqual(event["type"], events.EVENT_AUDIT_FAILED)
        self.assertEqual(event["payload"]["reason"], "non_equivalent")
        self.assertTrue(event["payload"]["evicted"])
        self.assertEqual(event["payload"]["rerank_score"], 0.81)


if __name__ == "__main__":
    unittest.main()
