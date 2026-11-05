import unittest
from unittest.mock import patch

from app.core import anonymous_telemetry
from app.core.config import config


def _fake_client(captured, side_effect=None):
    class FakeResp:
        status_code = 202

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, *, json=None):
            if side_effect is not None:
                raise side_effect
            captured["url"] = url
            captured["json"] = json
            return FakeResp()

    return FakeClient


class TestAnonymousTelemetry(unittest.TestCase):
    def setUp(self):
        self._saved = {
            "enabled": config.TELEMETRY_ENABLED,
            "endpoint": config.TELEMETRY_ENDPOINT,
            "instance_id": config.TELEMETRY_INSTANCE_ID,
            "version": config.VERSION,
            "mode": config.TELEMETRY_DEPLOYMENT_MODE,
        }
        config.TELEMETRY_ENABLED = True
        config.TELEMETRY_ENDPOINT = "http://telemetry.example/ping"
        config.TELEMETRY_INSTANCE_ID = "test-instance-123"
        config.VERSION = "0.1.0"
        config.TELEMETRY_DEPLOYMENT_MODE = "self-hosted"

    def tearDown(self):
        config.TELEMETRY_ENABLED = self._saved["enabled"]
        config.TELEMETRY_ENDPOINT = self._saved["endpoint"]
        config.TELEMETRY_INSTANCE_ID = self._saved["instance_id"]
        config.VERSION = self._saved["version"]
        config.TELEMETRY_DEPLOYMENT_MODE = self._saved["mode"]

    def test_ping_payload_shape(self):
        captured = {}

        with patch(
            "httpx.AsyncClient",
            _fake_client(captured),
        ):
            import asyncio

            asyncio.run(anonymous_telemetry._ping_anonymous_usage("startup"))

        self.assertEqual(captured["url"], "http://telemetry.example/ping")
        self.assertEqual(captured["json"]["version"], "0.1.0")
        self.assertEqual(captured["json"]["instance_id"], "test-instance-123")
        self.assertEqual(captured["json"]["deployment_mode"], "self-hosted")
        self.assertEqual(captured["json"]["event"], "startup")
        # No sensitive fields leak.
        self.assertNotIn("prompt", captured["json"])
        self.assertNotIn("tenant_id", captured["json"])

    def test_ping_fail_open_on_network_error(self):
        with patch(
            "httpx.AsyncClient",
            _fake_client({}, side_effect=RuntimeError("network down")),
        ):
            import asyncio

            # Must not raise.
            asyncio.run(anonymous_telemetry._ping_anonymous_usage("heartbeat"))

    def test_no_endpoint_is_noop(self):
        config.TELEMETRY_ENDPOINT = ""
        captured = {}
        with patch("httpx.AsyncClient", _fake_client(captured)):
            import asyncio

            asyncio.run(anonymous_telemetry._ping_anonymous_usage("startup"))
        self.assertEqual(captured, {})

    def test_schedule_returns_none_when_disabled(self):
        config.TELEMETRY_ENABLED = False
        self.assertIsNone(anonymous_telemetry.schedule_anonymous_telemetry())


if __name__ == "__main__":
    unittest.main()
