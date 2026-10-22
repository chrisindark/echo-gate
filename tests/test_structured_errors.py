import unittest

from fastapi import HTTPException

from app.core.circuit_breaker import CircuitBreakerOpenException
from app.core.error_classification import (
    PROVIDER_ERROR_AUTH,
    PROVIDER_ERROR_CONTENT_FILTER,
    PROVIDER_ERROR_QUOTA_EXCEEDED,
    PROVIDER_ERROR_RATE_LIMIT,
    PROVIDER_ERROR_TIMEOUT,
    PROVIDER_ERROR_UNAVAILABLE,
    classify_provider_error,
)


class TestStructuredErrors(unittest.TestCase):
    def test_classify_timeout(self):
        err = classify_provider_error(TimeoutError(), provider="openai")
        self.assertEqual(err["error_code"], PROVIDER_ERROR_TIMEOUT)
        self.assertEqual(err["status_code"], 504)
        self.assertTrue(err["retryable"])

    def test_classify_content_filter(self):
        exc = HTTPException(
            status_code=400, detail="The prompt was blocked by safety filters."
        )
        err = classify_provider_error(exc, provider="gemini")
        self.assertEqual(err["error_code"], PROVIDER_ERROR_CONTENT_FILTER)
        self.assertEqual(err["status_code"], 400)
        self.assertFalse(err["retryable"])

    def test_classify_rate_limit(self):
        exc = HTTPException(status_code=429, detail="Rate limit exceeded.")
        err = classify_provider_error(exc, provider="groq")
        self.assertEqual(err["error_code"], PROVIDER_ERROR_RATE_LIMIT)
        self.assertEqual(err["status_code"], 429)
        self.assertTrue(err["retryable"])

    def test_classify_quota_exceeded(self):
        exc = HTTPException(
            status_code=429,
            detail="You exceeded your current quota, please check your plan and billing.",
        )
        err = classify_provider_error(exc, provider="openai")
        self.assertEqual(err["error_code"], PROVIDER_ERROR_QUOTA_EXCEEDED)
        self.assertEqual(err["status_code"], 429)
        self.assertFalse(err["retryable"])

    def test_classify_auth_failure(self):
        exc = HTTPException(status_code=401, detail="Incorrect API key provided.")
        err = classify_provider_error(exc, provider="anthropic")
        self.assertEqual(err["error_code"], PROVIDER_ERROR_AUTH)
        self.assertEqual(err["status_code"], 401)
        self.assertFalse(err["retryable"])

    def test_classify_unavailable(self):
        exc = HTTPException(
            status_code=503, detail="The server is temporarily overloaded."
        )
        err = classify_provider_error(exc, provider="openrouter")
        self.assertEqual(err["error_code"], PROVIDER_ERROR_UNAVAILABLE)
        self.assertEqual(err["status_code"], 503)
        self.assertTrue(err["retryable"])

    def test_circuit_open_exception_is_structured(self):
        exc = CircuitBreakerOpenException("openai", retry_after=12.4)
        self.assertEqual(exc.status_code, 503)
        self.assertEqual(exc.detail["error_code"], PROVIDER_ERROR_UNAVAILABLE)
        self.assertEqual(exc.detail["provider"], "openai")
        self.assertIn("circuit breaker is OPEN", exc.detail["message"])
        self.assertEqual(exc.headers["Retry-After"], "12")

    def test_circuit_open_exception_classifies_as_unavailable(self):
        # A client that already handles provider failures keeps working.
        err = classify_provider_error(
            CircuitBreakerOpenException("openai", retry_after=0.2), provider="openai"
        )
        self.assertEqual(err["error_code"], PROVIDER_ERROR_UNAVAILABLE)
        self.assertEqual(err["status_code"], 503)
        self.assertTrue(err["retryable"])


if __name__ == "__main__":
    unittest.main()
