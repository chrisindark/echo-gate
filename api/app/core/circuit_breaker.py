import logging
import time
from enum import Enum

from fastapi import HTTPException

from app.core.config import config
from app.core.retry import is_retryable_exception

logger = logging.getLogger(__name__)


class CircuitBreakerState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenException(HTTPException):
    def __init__(self, service_name: str, retry_after: float):
        super().__init__(
            status_code=503,
            detail=f"Circuit breaker is OPEN for provider '{service_name}'. Upstream service is degraded.",
            headers={"Retry-After": str(int(max(1.0, retry_after)))},
        )
        self.service_name = service_name
        self.retry_after = retry_after


class CircuitBreaker:
    """Per-provider circuit breaker protecting against upstream cascade failures.

    Transitions:
    - CLOSED -> OPEN: after consecutive failures >= failure_threshold (from retryable errors).
    - OPEN -> HALF_OPEN: after recovery_timeout elapses.
    - HALF_OPEN -> CLOSED: after consecutive probe successes >= half_open_success_threshold.
    - HALF_OPEN -> OPEN: on any single failure during probe.
    """

    def __init__(
        self,
        service_name: str,
        failure_threshold: int | None = None,
        recovery_timeout: float | None = None,
        half_open_success_threshold: int | None = None,
    ) -> None:
        self.service_name = service_name
        self.failure_threshold = (
            failure_threshold
            if failure_threshold is not None
            else config.CIRCUIT_BREAKER_FAILURE_THRESHOLD
        )
        self.recovery_timeout = (
            recovery_timeout
            if recovery_timeout is not None
            else config.CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS
        )
        self.half_open_success_threshold = (
            half_open_success_threshold
            if half_open_success_threshold is not None
            else config.CIRCUIT_BREAKER_HALF_OPEN_SUCCESS_THRESHOLD
        )

        self._state: CircuitBreakerState = CircuitBreakerState.CLOSED
        self._consecutive_failures: int = 0
        self._consecutive_successes: int = 0
        self._last_failure_time: float = 0.0
        self._last_state_change: float = time.monotonic()
        self._half_open_probe_in_flight: bool = False

    @property
    def state(self) -> CircuitBreakerState:
        self._evaluate_state()
        return self._state

    def _evaluate_state(self) -> None:
        if self._state == CircuitBreakerState.OPEN:
            now = time.monotonic()
            if now - self._last_failure_time >= self.recovery_timeout:
                logger.info(
                    "Circuit breaker for '%s' transitioning OPEN -> HALF_OPEN after %.1fs",
                    self.service_name,
                    now - self._last_failure_time,
                )
                self._state = CircuitBreakerState.HALF_OPEN
                self._consecutive_successes = 0
                self._half_open_probe_in_flight = False
                self._last_state_change = now

    def can_execute(self) -> bool:
        """Return True if a request to this provider is permitted."""
        if not getattr(config, "CIRCUIT_BREAKER_ENABLED", True):
            return True

        self._evaluate_state()
        if self._state == CircuitBreakerState.CLOSED:
            return True
        if self._state == CircuitBreakerState.HALF_OPEN:
            if not self._half_open_probe_in_flight:
                self._half_open_probe_in_flight = True
                return True
            return False
        # State is OPEN
        return False

    def remaining_recovery_time(self) -> float:
        """Return remaining seconds until the breaker attempts recovery."""
        if self._state != CircuitBreakerState.OPEN:
            return 0.0
        elapsed = time.monotonic() - self._last_failure_time
        return max(0.0, self.recovery_timeout - elapsed)

    def check_available(self) -> None:
        """Raise CircuitBreakerOpenException if the breaker is open."""
        if not self.can_execute():
            raise CircuitBreakerOpenException(
                service_name=self.service_name,
                retry_after=self.remaining_recovery_time(),
            )

    def release_probe(self) -> None:
        """Release a HALF_OPEN probe permit that was never resolved.

        ``can_execute`` claims the single HALF_OPEN probe slot before dispatch.
        If the attempt never reaches the provider -- a saturated bulkhead, a
        cancelled request, an exception between the claim and the call -- no
        success or failure is recorded, so the claim must be released explicitly.
        Otherwise the breaker stays HALF_OPEN with its only permit held forever
        and the provider is skipped until the process restarts.
        """
        self._half_open_probe_in_flight = False

    def record_success(self) -> None:
        """Record a successful provider interaction."""
        self._evaluate_state()
        now = time.monotonic()

        if self._state == CircuitBreakerState.HALF_OPEN:
            self._consecutive_successes += 1
            self._half_open_probe_in_flight = False
            if self._consecutive_successes >= self.half_open_success_threshold:
                logger.info(
                    "Circuit breaker for '%s' transitioning HALF_OPEN -> CLOSED (%d probe successes)",
                    self.service_name,
                    self._consecutive_successes,
                )
                self._state = CircuitBreakerState.CLOSED
                self._consecutive_failures = 0
                self._consecutive_successes = 0
                self._last_state_change = now
        elif self._state == CircuitBreakerState.CLOSED:
            self._consecutive_failures = 0

    def record_failure(self, exc: BaseException | None = None) -> None:
        """Record a provider failure. Deterministic errors (e.g. 400 bad request) do not trip."""
        if exc is not None and not is_retryable_exception(exc):
            # Client errors / non-retryable exceptions do not reflect provider unhealthiness
            return

        now = time.monotonic()
        self._last_failure_time = now

        if self._state == CircuitBreakerState.HALF_OPEN:
            logger.warning(
                "Circuit breaker probe for '%s' failed. Tripping back to OPEN.",
                self.service_name,
            )
            self._state = CircuitBreakerState.OPEN
            self._half_open_probe_in_flight = False
            self._consecutive_successes = 0
            self._last_state_change = now
        elif self._state == CircuitBreakerState.CLOSED:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                logger.warning(
                    "Circuit breaker for '%s' tripped OPEN after %d consecutive failures.",
                    self.service_name,
                    self._consecutive_failures,
                )
                self._state = CircuitBreakerState.OPEN
                self._last_state_change = now

    def reset(self) -> None:
        """Force reset the breaker to CLOSED."""
        self._state = CircuitBreakerState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_failure_time = 0.0
        self._half_open_probe_in_flight = False
        self._last_state_change = time.monotonic()


class CircuitBreakerRegistry:
    """Registry managing CircuitBreakers per provider."""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}

    def get_breaker(
        self,
        service_name: str,
        failure_threshold: int | None = None,
        recovery_timeout: float | None = None,
    ) -> CircuitBreaker:
        normalized = service_name.strip().lower()
        if normalized not in self._breakers:
            self._breakers[normalized] = CircuitBreaker(
                service_name=normalized,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
            )
        return self._breakers[normalized]

    def is_service_available(self, service_name: str) -> bool:
        return self.get_breaker(service_name).can_execute()

    def reset_all(self) -> None:
        for breaker in self._breakers.values():
            breaker.reset()
        self._breakers.clear()


circuit_breaker_registry = CircuitBreakerRegistry()
