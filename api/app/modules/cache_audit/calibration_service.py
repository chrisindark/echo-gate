"""Conformal Risk Control calibration service for semantic cache intent thresholds.

Analyzes ground-truth audit evaluations (cache_evaluations table) grouped by intent
and computes empirical threshold quantiles guaranteeing an error rate <= alpha (e.g. <= 2%).
Applies calibrated thresholds as non-destructive Redis overrides (`cache:thresholds:{intent}`)
rather than modifying code or restarting services.
"""

import json
import logging
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import config
from app.core.database import SessionFactory, task_db_session
from app.modules.cache_audit.cache_audit_model import CacheEvaluation
from app.modules.cache_audit.cache_audit_schema import (
    IntentCalibrationResult,
    IntentThresholdOverride,
)
from app.modules.redis.redis_service import RedisService

logger = logging.getLogger(__name__)

REDIS_THRESHOLD_PREFIX = "cache:thresholds:"
REDIS_INTENTS_SET_KEY = "cache:thresholds:all_intents"
DEFAULT_OVERRIDE_TTL_SECONDS = 7 * 86400  # 7 days


def threshold_redis_key(intent: str) -> str:
    return f"{REDIS_THRESHOLD_PREFIX}{intent}"


def compute_conformal_threshold(
    scores: list[float],
    is_equivalent: list[bool],
    alpha: float,
    default_threshold: float,
    min_bound: float = 0.70,
    max_bound: float = 0.99,
) -> tuple[float, float, int]:
    """Compute empirical threshold quantile guaranteeing error rate <= alpha.

    Args:
        scores: Rerank scores of evaluated audit samples.
        is_equivalent: Ground-truth equivalence verdicts (True = good hit, False = error).
        alpha: Maximum acceptable error rate (fraction of non-equivalent hits among accepted).
        default_threshold: Fallback threshold if no candidate satisfies the constraint.
        min_bound: Minimum allowable threshold floor.
        max_bound: Maximum allowable threshold cap.

    Returns:
        (calibrated_threshold, empirical_error_rate, error_count)
    """
    if not scores or not is_equivalent:
        return default_threshold, 0.0, 0

    n = len(scores)
    false_positives = [scores[i] for i in range(n) if not is_equivalent[i]]

    # Case 1: Zero false positives. A clean sample is not evidence the gate can
    # move down; lowering it to the minimum observed score would admit weaker
    # hits than the configured default. Keep the default.
    if not false_positives:
        return round(default_threshold, 4), 0.0, 0

    # Case 2: Sweep candidate thresholds from min_bound to max_bound in 0.005 steps,
    # augmented with the exact observed score values.
    candidates = set()
    step = 0.005
    curr = min_bound
    while curr <= max_bound:
        candidates.add(round(curr, 4))
        curr += step
    for s in scores:
        if min_bound <= s <= max_bound:
            candidates.add(round(s, 4))

    sorted_candidates = sorted(candidates)

    best_threshold = max_bound
    best_error_rate = 1.0
    best_error_count = len(false_positives)
    found = False

    # Find the smallest threshold tau such that error rate <= alpha
    for tau in sorted_candidates:
        accepted_indices = [i for i in range(n) if scores[i] >= tau]
        n_accepted = len(accepted_indices)
        if n_accepted == 0:
            continue

        n_errors = sum(1 for i in accepted_indices if not is_equivalent[i])
        error_rate = n_errors / n_accepted

        if error_rate <= alpha:
            best_threshold = tau
            best_error_rate = error_rate
            best_error_count = n_errors
            found = True
            break

    if not found:
        # If even at max_bound the error rate exceeds alpha, cap at max_bound
        best_threshold = max_bound
        accepted_indices = [i for i in range(n) if scores[i] >= max_bound]
        n_accepted = len(accepted_indices)
        best_error_count = sum(1 for i in accepted_indices if not is_equivalent[i])
        best_error_rate = best_error_count / n_accepted if n_accepted > 0 else 0.0

    return round(best_threshold, 4), round(best_error_rate, 4), best_error_count


class IntentCalibrationService:
    """Service to calibrate intent confidence thresholds from audit evaluations."""

    def __init__(
        self,
        session_factory: SessionFactory | Session | None = None,
        redis_service: RedisService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.redis_service = redis_service

    @contextmanager
    def _get_session(self) -> Generator[Session]:
        if isinstance(self._session_factory, Session):
            yield self._session_factory
        elif self._session_factory is not None:
            with self._session_factory() as s:
                yield s
        else:
            with task_db_session() as s:
                yield s

    def calibrate_intent(
        self,
        intent: str,
        alpha: float | None = None,
        moderate_alpha: float | None = None,
        min_samples: int | None = None,
    ) -> IntentCalibrationResult:
        """Calibrate threshold overrides for a single intent.

        Args:
            intent: Intent name string (e.g. 'code_generation').
            alpha: High confidence error bound (default from config or 0.02).
            moderate_alpha: Moderate confidence error bound (default 0.05).
            min_samples: Minimum required audit records to calibrate (default 10).
        """
        target_alpha = (
            alpha
            if alpha is not None
            else getattr(config, "CACHE_CALIBRATION_ALPHA", 0.02)
        )
        mod_alpha = (
            moderate_alpha
            if moderate_alpha is not None
            else getattr(config, "CACHE_CALIBRATION_MODERATE_ALPHA", 0.05)
        )
        min_n = (
            min_samples
            if min_samples is not None
            else getattr(config, "CACHE_CALIBRATION_MIN_SAMPLES", 10)
        )

        default_thresholds = config.CACHE_CONFIDENCE_THRESHOLDS.get(
            intent,
            config.CACHE_CONFIDENCE_THRESHOLDS.get(
                "general_query", {"high": 0.95, "moderate": 0.88}
            ),
        )
        default_high = default_thresholds["high"]
        default_moderate = default_thresholds["moderate"]

        with self._get_session() as session:
            stmt = select(CacheEvaluation).where(
                CacheEvaluation.intent == intent,
                CacheEvaluation.rerank_score.isnot(None),
                CacheEvaluation.is_equivalent.isnot(None),
            )
            rows = session.scalars(stmt).all()

        scores = [float(r.rerank_score) for r in rows if r.rerank_score is not None]
        equivalence = [
            bool(r.is_equivalent) for r in rows if r.rerank_score is not None
        ]
        total_samples = len(scores)

        now = datetime.now(UTC)

        if total_samples < min_n:
            return IntentCalibrationResult(
                intent=intent,
                high=default_high,
                moderate=default_moderate,
                sample_count=total_samples,
                error_count=sum(1 for eq in equivalence if not eq),
                empirical_error_rate=0.0,
                target_alpha=target_alpha,
                calibrated=False,
                reason=f"Insufficient ground-truth samples ({total_samples} < {min_n})",
                calibrated_at=now,
                applied_to_redis=False,
            )

        high_thresh, high_err_rate, high_err_count = compute_conformal_threshold(
            scores=scores,
            is_equivalent=equivalence,
            alpha=target_alpha,
            default_threshold=default_high,
        )

        mod_thresh, _, _ = compute_conformal_threshold(
            scores=scores,
            is_equivalent=equivalence,
            alpha=mod_alpha,
            default_threshold=default_moderate,
        )

        # Ensure moderate threshold is strictly bounded by high threshold
        mod_thresh = min(mod_thresh, high_thresh)

        return IntentCalibrationResult(
            intent=intent,
            high=high_thresh,
            moderate=mod_thresh,
            sample_count=total_samples,
            error_count=high_err_count,
            empirical_error_rate=high_err_rate,
            target_alpha=target_alpha,
            calibrated=True,
            reason=None,
            calibrated_at=now,
            applied_to_redis=False,
        )

    def calibrate_all_intents(
        self,
        alpha: float | None = None,
        moderate_alpha: float | None = None,
        min_samples: int | None = None,
    ) -> dict[str, IntentCalibrationResult]:
        """Calibrate thresholds across all intents observed in audit evaluations."""
        with self._get_session() as session:
            stmt = (
                select(CacheEvaluation.intent)
                .where(
                    CacheEvaluation.intent.isnot(None),
                    CacheEvaluation.rerank_score.isnot(None),
                    CacheEvaluation.is_equivalent.isnot(None),
                )
                .distinct()
            )
            audited_intents = [row[0] for row in session.execute(stmt).all() if row[0]]

        all_intents = set(audited_intents)
        for intent_name in config.CACHE_CONFIDENCE_THRESHOLDS:
            all_intents.add(intent_name)

        results: dict[str, IntentCalibrationResult] = {}
        for intent in sorted(all_intents):
            results[intent] = self.calibrate_intent(
                intent=intent,
                alpha=alpha,
                moderate_alpha=moderate_alpha,
                min_samples=min_samples,
            )

        return results

    async def apply_overrides_to_redis(
        self,
        results: dict[str, IntentCalibrationResult],
        ttl_seconds: int = DEFAULT_OVERRIDE_TTL_SECONDS,
    ) -> int:
        """Persist calibrated intent threshold overrides to Redis."""
        if not self.redis_service:
            logger.warning(
                "No RedisService configured; skipping threshold override persistence."
            )
            return 0

        applied_count = 0
        for intent, res in results.items():
            if not res.calibrated:
                continue

            override = IntentThresholdOverride(
                intent=intent,
                high=res.high,
                moderate=res.moderate,
                sample_count=res.sample_count,
                error_count=res.error_count,
                error_rate=res.empirical_error_rate,
                alpha=res.target_alpha,
                calibrated_at=res.calibrated_at,
            )

            key = threshold_redis_key(intent)
            payload = override.model_dump_json()

            try:
                await self.redis_service.set(key, payload, ttl=ttl_seconds)
                # Track in set of all calibrated intents
                if hasattr(self.redis_service.redis, "sadd"):
                    await self.redis_service.redis.sadd(REDIS_INTENTS_SET_KEY, intent)
                res.applied_to_redis = True
                applied_count += 1
                logger.info(
                    "Applied calibrated threshold to Redis: %s -> high=%.4f moderate=%.4f (samples=%d, err=%.2f%%)",
                    key,
                    res.high,
                    res.moderate,
                    res.sample_count,
                    res.empirical_error_rate * 100,
                )
            except Exception:
                logger.exception(
                    "Failed to write calibrated threshold %s to Redis", key
                )

        return applied_count

    async def get_redis_override(self, intent: str) -> IntentThresholdOverride | None:
        """Fetch the active Redis threshold override for an intent if present."""
        if not self.redis_service:
            return None

        try:
            raw = await self.redis_service.get(threshold_redis_key(intent))
            if not raw:
                return None
            data = json.loads(raw)
            return IntentThresholdOverride(**data)
        except Exception:
            logger.exception(
                "Failed to read threshold override from Redis for %s", intent
            )
            return None

    async def get_all_redis_overrides(self) -> dict[str, IntentThresholdOverride]:
        """Fetch all active Redis threshold overrides."""
        if not self.redis_service:
            return {}

        overrides: dict[str, IntentThresholdOverride] = {}
        try:
            intents = []
            if hasattr(self.redis_service.redis, "smembers"):
                intents = list(
                    await self.redis_service.redis.smembers(REDIS_INTENTS_SET_KEY)
                )

            for intent in intents:
                override = await self.get_redis_override(intent)
                if override:
                    overrides[intent] = override
        except Exception:
            logger.exception("Failed to read all threshold overrides from Redis")

        return overrides

    async def delete_redis_override(self, intent: str) -> bool:
        """Delete an intent's override from Redis, reverting to default config thresholds."""
        if not self.redis_service:
            return False

        try:
            key = threshold_redis_key(intent)
            await self.redis_service.delete(key)
            if hasattr(self.redis_service.redis, "srem"):
                await self.redis_service.redis.srem(REDIS_INTENTS_SET_KEY, intent)
            logger.info("Deleted threshold override from Redis for %s", intent)
            return True
        except Exception:
            logger.exception(
                "Failed to delete threshold override from Redis for %s", intent
            )
            return False
