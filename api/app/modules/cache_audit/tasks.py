"""Celery tasks for the cache audit loop.

Includes periodic intent threshold calibration (Conformal Risk Control).
"""

import logging
from typing import Any

from app.core.database import SessionLocal
from app.modules.cache_audit.calibration_service import IntentCalibrationService
from app.modules.redis.redis_service import RedisService
from app.worker.celery_app import celery_app, run_async

logger = logging.getLogger(__name__)


async def _calibrate_intent_thresholds_async() -> dict[str, Any]:
    redis_service = RedisService()
    try:
        calibrator = IntentCalibrationService(
            session_factory=SessionLocal,
            redis_service=redis_service,
        )
        results = calibrator.calibrate_all_intents()
        applied = await calibrator.apply_overrides_to_redis(results)
        logger.info(
            "Periodic intent threshold calibration completed: %d overrides applied",
            applied,
        )
        return {
            "intents_evaluated": len(results),
            "overrides_applied": applied,
            "results": {
                intent: {
                    "high": res.high,
                    "moderate": res.moderate,
                    "calibrated": res.calibrated,
                    "sample_count": res.sample_count,
                    "error_rate": res.empirical_error_rate,
                }
                for intent, res in results.items()
            },
        }
    finally:
        await redis_service.close()


def execute_calibrate_intent_thresholds_job() -> dict[str, Any]:
    try:
        return run_async(_calibrate_intent_thresholds_async())
    except Exception:
        logger.exception("Failed to execute calibrate_intent_thresholds job")
        return {"error": "calibration_failed"}


@celery_app.task(name="calibrate_intent_thresholds")
def calibrate_intent_thresholds() -> dict[str, Any]:
    return execute_calibrate_intent_thresholds_job()
