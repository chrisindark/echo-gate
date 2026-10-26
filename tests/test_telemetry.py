import unittest
from unittest.mock import MagicMock

from opentelemetry.trace import StatusCode

from app.core.config import config
from app.core.telemetry import (
    ECHOGATE_CACHE_HIT,
    ECHOGATE_ROUTING_DECISION,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_SYSTEM,
    SPAN_GENERATE_COMPLETION,
    get_tracer,
    set_span_attributes,
    trace_span,
)


class TestTelemetry(unittest.TestCase):
    def setUp(self):
        self._saved_otel_enabled = config.OTEL_ENABLED
        config.OTEL_ENABLED = True

    def tearDown(self):
        config.OTEL_ENABLED = self._saved_otel_enabled

    def test_semantic_conventions_constants(self):
        self.assertEqual(GEN_AI_SYSTEM, "gen_ai.system")
        self.assertEqual(GEN_AI_REQUEST_MODEL, "gen_ai.request.model")
        self.assertEqual(ECHOGATE_CACHE_HIT, "echogate.cache.hit")
        self.assertEqual(ECHOGATE_ROUTING_DECISION, "echogate.routing_decision")
        self.assertEqual(SPAN_GENERATE_COMPLETION, "echogate.generate_completion")

    def test_get_tracer(self):
        tracer = get_tracer("test-tracer")
        self.assertIsNotNone(tracer)

    def test_trace_span_context_normal(self):
        with trace_span(SPAN_GENERATE_COMPLETION, {GEN_AI_SYSTEM: "openai"}) as span:
            self.assertIsNotNone(span)

    def test_trace_span_records_exception(self):
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = (
            mock_span
        )

        with self.assertRaises(ValueError):
            with trace_span("failing-op", tracer=mock_tracer):
                raise ValueError("provider boom")

        mock_span.record_exception.assert_called_once()
        mock_span.set_status.assert_called_with(StatusCode.ERROR, "provider boom")

    def test_trace_span_disabled(self):
        config.OTEL_ENABLED = False
        with trace_span("disabled-op") as span:
            # When disabled, invalid span is yielded and no error is raised
            self.assertFalse(span.get_span_context().is_valid)

    def test_set_span_attributes_safe(self):
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True

        set_span_attributes(
            mock_span,
            {
                GEN_AI_REQUEST_MODEL: "gemini-2.5-flash",
                "empty_field": None,
            },
        )
        mock_span.set_attribute.assert_called_once_with(
            GEN_AI_REQUEST_MODEL, "gemini-2.5-flash"
        )


if __name__ == "__main__":
    unittest.main()
