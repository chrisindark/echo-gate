"""Admin CLI for calibrating semantic cache intent thresholds via Conformal Risk Control.

Analyzes ground-truth audit evaluations (cache_evaluations table) grouped by intent,
computes empirical threshold quantiles guaranteeing an error rate <= alpha,
and applies them as Redis overrides (cache:thresholds:{intent}).

Usage:
    # Dry run review across all intents:
    poetry run python -m app.scripts.calibrate_intent_thresholds

    # Calibrate with custom alpha (e.g. 1% error rate) and min 20 samples:
    poetry run python -m app.scripts.calibrate_intent_thresholds --alpha 0.01 --min-samples 20

    # Apply calibrated thresholds to Redis:
    poetry run python -m app.scripts.calibrate_intent_thresholds --apply

    # Calibrate a single intent and apply:
    poetry run python -m app.scripts.calibrate_intent_thresholds --intent code_generation --apply

    # Clear an intent's override in Redis (reverting to config defaults):
    poetry run python -m app.scripts.calibrate_intent_thresholds --clear-intent code_generation
"""

import argparse
import asyncio
import sys

from app.core.config import config
from app.modules.cache_audit.calibration_service import (
    IntentCalibrationResult,
    IntentCalibrationService,
)
from app.modules.redis.redis_service import RedisService


def _format_table(results: dict[str, IntentCalibrationResult], applied: bool) -> str:
    lines = []
    lines.append("=" * 105)
    lines.append(
        f"{'INTENT':<22} | {'SAMPLES':<7} | {'ERRORS':<6} | {'ERR RATE':<8} | {'DEFAULT (H/M)':<15} | {'CALIBRATED (H/M)':<17} | {'STATUS'}"
    )
    lines.append("=" * 105)

    for intent, res in sorted(results.items()):
        default_th = config.CACHE_CONFIDENCE_THRESHOLDS.get(
            intent,
            config.CACHE_CONFIDENCE_THRESHOLDS.get(
                "general_query", {"high": 0.95, "moderate": 0.88}
            ),
        )
        def_str = f"{default_th['high']:.2f} / {default_th['moderate']:.2f}"
        cal_str = f"{res.high:.4f} / {res.moderate:.4f}"
        err_pct = f"{res.empirical_error_rate * 100:.1f}%"

        if res.calibrated:
            status = "APPLIED TO REDIS" if applied else "CALIBRATED (dry-run)"
        else:
            status = f"SKIPPED ({res.reason or 'insufficient data'})"

        lines.append(
            f"{intent:<22} | {res.sample_count:<7} | {res.error_count:<6} | {err_pct:<8} | {def_str:<15} | {cal_str:<17} | {status}"
        )

    lines.append("=" * 105)
    return "\n".join(lines)


async def _async_main(args: argparse.Namespace) -> int:
    redis_service = RedisService()
    try:
        # No session_factory: task_db_session applies system RLS. SessionLocal
        # here would only see the anonymous tenant.
        calibrator = IntentCalibrationService(redis_service=redis_service)

        if args.clear_intent:
            success = await calibrator.delete_redis_override(args.clear_intent)
            if success:
                print(
                    f"Successfully deleted Redis override for intent: {args.clear_intent}"
                )
                return 0
            else:
                print(
                    f"Failed to delete Redis override for intent: {args.clear_intent}",
                    file=sys.stderr,
                )
                return 1

        if args.clear_all:
            overrides = await calibrator.get_all_redis_overrides()
            for intent in overrides:
                await calibrator.delete_redis_override(intent)
            print(f"Cleared {len(overrides)} Redis overrides.")
            return 0

        # Run calibration
        if args.intent:
            result = calibrator.calibrate_intent(
                intent=args.intent,
                alpha=args.alpha,
                moderate_alpha=args.moderate_alpha,
                min_samples=args.min_samples,
            )
            results = {args.intent: result}
        else:
            results = calibrator.calibrate_all_intents(
                alpha=args.alpha,
                moderate_alpha=args.moderate_alpha,
                min_samples=args.min_samples,
            )

        if args.apply:
            applied_count = await calibrator.apply_overrides_to_redis(results)
            print(_format_table(results, applied=True))
            print(
                f"\nSuccessfully applied {applied_count} intent threshold overrides to Redis."
            )
        else:
            print(_format_table(results, applied=False))
            print(
                "\nDry-run completed. Pass --apply to write calibrated overrides to Redis."
            )

        return 0
    finally:
        await redis_service.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate semantic cache confidence thresholds using Conformal Risk Control."
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=getattr(config, "CACHE_CALIBRATION_ALPHA", 0.02),
        help="Target high-confidence error rate (default: 0.02 = 2%% error bound).",
    )
    parser.add_argument(
        "--moderate-alpha",
        type=float,
        default=getattr(config, "CACHE_CALIBRATION_MODERATE_ALPHA", 0.05),
        help="Target moderate-confidence error rate (default: 0.05 = 5%% error bound).",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=getattr(config, "CACHE_CALIBRATION_MIN_SAMPLES", 10),
        help="Minimum required audit samples to calibrate an intent (default: 10).",
    )
    parser.add_argument(
        "--intent",
        type=str,
        default=None,
        help="Calibrate only a single specific intent (e.g. 'code_generation').",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist calibrated thresholds to Redis at cache:thresholds:{intent}.",
    )
    parser.add_argument(
        "--clear-intent",
        type=str,
        default=None,
        help="Delete the Redis override for a specific intent.",
    )
    parser.add_argument(
        "--clear-all",
        action="store_true",
        help="Delete all intent overrides from Redis.",
    )

    args = parser.parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
