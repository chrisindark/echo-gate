import time
import unittest

from fastapi import HTTPException

from app.core.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenException,
    CircuitBreakerRegistry,
    CircuitBreakerState,
)


class TestCircuitBreaker(unittest.TestCase):
    def setUp(self):
        self.breaker = CircuitBreaker(
            service_name="test_provider",
            failure_threshold=3,
            recovery_timeout=0.2,  # fast recovery for testing
            half_open_success_threshold=2,
        )

    def test_initial_state_is_closed(self):
        self.assertEqual(self.breaker.state, CircuitBreakerState.CLOSED)
        self.assertTrue(self.breaker.can_execute())

    def test_consecutive_failures_trips_to_open(self):
        # Retryable exceptions (e.g. 503, 500, timeout)
        exc = HTTPException(status_code=503, detail="Service Unavailable")
        self.breaker.record_failure(exc)
        self.assertEqual(self.breaker.state, CircuitBreakerState.CLOSED)
        self.breaker.record_failure(exc)
        self.assertEqual(self.breaker.state, CircuitBreakerState.CLOSED)
        self.breaker.record_failure(exc)
        self.assertEqual(self.breaker.state, CircuitBreakerState.OPEN)
        self.assertFalse(self.breaker.can_execute())

        # Calling check_available raises CircuitBreakerOpenException with 503
        with self.assertRaises(CircuitBreakerOpenException) as ctx:
            self.breaker.check_available()
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("Retry-After", ctx.exception.headers)

    def test_non_retryable_failure_does_not_trip_breaker(self):
        # 400 Bad Request should not trip circuit breaker
        exc = HTTPException(status_code=400, detail="Bad Request")
        for _ in range(5):
            self.breaker.record_failure(exc)
        self.assertEqual(self.breaker.state, CircuitBreakerState.CLOSED)
        self.assertTrue(self.breaker.can_execute())

    def test_open_to_half_open_transition(self):
        exc = HTTPException(status_code=500, detail="Internal Server Error")
        for _ in range(3):
            self.breaker.record_failure(exc)
        self.assertEqual(self.breaker.state, CircuitBreakerState.OPEN)

        # Wait for recovery timeout
        time.sleep(0.25)
        self.assertEqual(self.breaker.state, CircuitBreakerState.HALF_OPEN)
        self.assertTrue(self.breaker.can_execute())

    def test_half_open_failure_re_trips_to_open(self):
        exc = HTTPException(status_code=502, detail="Bad Gateway")
        for _ in range(3):
            self.breaker.record_failure(exc)
        self.assertEqual(self.breaker.state, CircuitBreakerState.OPEN)

        time.sleep(0.25)
        self.assertEqual(self.breaker.state, CircuitBreakerState.HALF_OPEN)

        # Failure during probe immediately trips back to OPEN
        self.breaker.record_failure(exc)
        self.assertEqual(self.breaker.state, CircuitBreakerState.OPEN)
        self.assertFalse(self.breaker.can_execute())

    def test_half_open_successes_close_breaker(self):
        exc = HTTPException(status_code=504, detail="Gateway Timeout")
        for _ in range(3):
            self.breaker.record_failure(exc)
        self.assertEqual(self.breaker.state, CircuitBreakerState.OPEN)

        time.sleep(0.25)
        self.assertEqual(self.breaker.state, CircuitBreakerState.HALF_OPEN)

        self.breaker.record_success()
        self.assertEqual(self.breaker.state, CircuitBreakerState.HALF_OPEN)

        self.breaker.record_success()
        self.assertEqual(self.breaker.state, CircuitBreakerState.CLOSED)
        self.assertTrue(self.breaker.can_execute())

    def test_registry_creates_and_manages_breakers(self):
        registry = CircuitBreakerRegistry()
        b1 = registry.get_breaker("OpenAI")
        b2 = registry.get_breaker("openai")
        self.assertIs(b1, b2)
        self.assertTrue(registry.is_service_available("openai"))
        registry.reset_all()


if __name__ == "__main__":
    unittest.main()
