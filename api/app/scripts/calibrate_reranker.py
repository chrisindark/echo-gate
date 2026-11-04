"""Offline calibration sweep for the semantic-cache reranker.

Two data sources:

* ``--source static`` (default) replays the labelled `TEST_DATASET` through the
  real `RerankerService` (cross-encoder + bounded signal factors) *without*
  needing Redis, Qdrant, the API, or the intent LLM. It simulates the sequential
  cache that `run_tests.py` produces (every earlier prompt is a candidate for
  the current prompt) and measures how well the composite `rerank_score`
  separates expected matches from non-matches.
* ``--source audit`` sweeps the live ground truth in `cache_evaluations`
  instead: every row with a recorded `rerank_score` and a judge verdict, which
  is what the shadow audit accumulates in production. No inference engine is
  needed (the scores are already stored), and the report adds a per-intent
  conformal recommendation computed with the same
  `compute_conformal_threshold` the scheduled calibration job uses.

Both sources sweep acceptance thresholds and report precision/recall/F1 so the
`CACHE_CONFIDENCE_THRESHOLDS` values can be tuned from data, and write:

  * ``reranker_calibration.csv``   - one row per sample
  * ``reranker_calibration.json``  - summary + recommended threshold

Caveat for the static source: intent classification and the LLM-extracted
metadata (core_operation, core_subject, subject_modifier, action_modifier) are
unavailable offline, so those factors are inert there (the request intent is
``None`` and the request metadata is empty, both of which are deliberately *not*
penalized). The sweep therefore calibrates the dominant cross-encoder + entity
signal. Use ``--source audit`` to calibrate from real traffic instead.

Usage:
    poetry run python -m app.scripts.calibrate_reranker
    poetry run python -m app.scripts.calibrate_reranker --limit 60 --max-candidates 40
    poetry run python -m app.scripts.calibrate_reranker --scoring-mode additive
    poetry run python -m app.scripts.calibrate_reranker --score-source base
    poetry run python -m app.scripts.calibrate_reranker --source audit
    poetry run python -m app.scripts.calibrate_reranker --source audit --intent code_generation
    poetry run python -m app.scripts.calibrate_reranker --source audit --alpha 0.01 --min-samples 20

The static source requires a running inference engine (`INFERENCE_BASE_URL`):
scoring goes through the same `CrossEncoderService` the gateway uses, so the
numbers match production. The audit source only needs the gateway database.
"""

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from typing import Any

from app.core.config import config
from app.modules.reranking.reranker_service import RerankerService
from app.modules.verifier.instruction_service import InstructionVerifier

# Mirrors the request shape used by `run_tests.py` so the calibration reflects
# real gateway traffic.
DEFAULT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_SERVICE = "google-genai"
DEFAULT_SYSTEM_PROMPT = (
    "You are a general purpose LLM answering questions for user queries"
)
DEFAULT_TEMPERATURE = 0.7

# Candidates are the answer to a *previous* prompt, so the cached user segment
# is the earlier prompt text.
_FACTOR_KEYS = (
    "model",
    "service",
    "intent",
    "metadata",
    "entities",
    "system_prompt",
    "temperature",
    "instruction",
)


class _EngineReranker:
    """`rerank_predict`-compatible adapter over the shared inference engine.

    Calibration must score with the same code path production serves, so this is
    the real `CrossEncoderService` rather than a locally loaded `CrossEncoder`.
    The model is whichever the engine maps to the `rerank` role; pin it with
    `INFERENCE_RERANK_MODEL` when the engine serves more than one.
    """

    def __init__(self) -> None:
        from app.modules.verifier.cross_encoder_service import CrossEncoderService

        self._service = CrossEncoderService()

    def rerank_predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return self._service.rerank_predict(pairs)


def _build_candidate(prompt: str, entities: list[str]) -> dict[str, Any]:
    return {
        "id": f"cand-{abs(hash(prompt)) % 10_000_000}",
        "score": 1.0,
        "payload": {
            "prompt": f"model:{DEFAULT_MODEL}|system: {DEFAULT_SYSTEM_PROMPT}|user: {prompt}",
            "user_prompt": prompt,
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "response": {
                "choices": [{"message": {"content": "cached answer"}}],
            },
            "model": DEFAULT_MODEL,
            "service_name": DEFAULT_SERVICE,
            "entities": entities,
            "temperature": DEFAULT_TEMPERATURE,
        },
    }


def _auc(positives: list[float], negatives: list[float]) -> float:
    """Rank-based AUC via pairwise comparison (Mann-Whitney U)."""
    if not positives or not negatives:
        return float("nan")
    wins = 0.0
    for p in positives:
        for n in negatives:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(positives) * len(negatives))


def _sweep(labels: list[bool], scores: list[float]) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    thresholds = sorted({0.0, 1.0, *[round(s, 4) for s in scores]})
    for threshold in thresholds:
        tp = fp = fn = tn = 0
        for label, score in zip(labels, scores, strict=False):
            predicted = score >= threshold
            if label and predicted:
                tp += 1
            elif label and not predicted:
                fn += 1
            elif not label and predicted:
                fp += 1
            else:
                tn += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )
        accuracy = (tp + tn) / len(labels) if labels else 0.0
        rows.append(
            {
                "threshold": threshold,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
                "accuracy": round(accuracy, 4),
                "accepted": tp + fp,
            }
        )
    return rows


def _best_threshold(rows: list[dict[str, float]], key: str = "f1") -> dict[str, float]:
    # Tie-break toward the highest threshold (fewest false positives) so we
    # prefer precision when F1 is otherwise equal.
    return max(rows, key=lambda r: (r[key], r["precision"], r["threshold"]))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=["static", "audit"],
        default="static",
        help=(
            "static (default): replay the labelled TEST_DATASET through the "
            "reranker. audit: sweep live cache_evaluations ground truth."
        ),
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Limit number of samples (0 = all)."
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="Cap the candidate pool to the most recent N (0 = all earlier prompts).",
    )
    parser.add_argument(
        "--scoring-mode",
        choices=["multiplicative", "additive"],
        default=None,
        help="Override RERANKER_SCORING_MODE for this run (static source only).",
    )
    parser.add_argument(
        "--score-source",
        choices=["rerank", "base"],
        default="rerank",
        help="Sweep on the composite rerank_score (default) or the raw base score.",
    )
    parser.add_argument(
        "--output-prefix",
        default="reranker_calibration",
        help="Prefix for the .csv and .json outputs.",
    )
    parser.add_argument(
        "--intent",
        default=None,
        help="Audit source only: restrict to one intent (e.g. 'code_generation').",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Audit source only: restrict to one model name.",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=getattr(config, "CACHE_CALIBRATION_ALPHA", 0.02),
        help=(
            "Audit source only: target error rate for the high threshold "
            "(default 0.02)."
        ),
    )
    parser.add_argument(
        "--moderate-alpha",
        type=float,
        default=getattr(config, "CACHE_CALIBRATION_MODERATE_ALPHA", 0.05),
        help="Audit source only: target error rate for the moderate threshold (0.05).",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=getattr(config, "CACHE_CALIBRATION_MIN_SAMPLES", 10),
        help=(
            "Audit source only: minimum records before an intent is reported as "
            "calibrated (default 10)."
        ),
    )
    return parser


def _run_static_calibration(args: argparse.Namespace) -> None:
    from app.modules.intent_classifier.entity_extractor_service import (
        EntityExtractorService,
    )
    from tests.datasets.prompt_dataset import TEST_DATASET

    tests = TEST_DATASET[: args.limit] if args.limit else TEST_DATASET
    print(
        f"Calibrating {len(tests)} queries | scoring_mode={config.RERANKER_SCORING_MODE} "
        f"| score_source={args.score_source}"
    )

    cross_encoder = _EngineReranker()
    reranker = RerankerService(cross_encoder, InstructionVerifier())
    entity_extractor = EntityExtractorService()

    # Simulated cache of earlier prompts: (prompt, canonical_id, entities).
    cache: list[tuple[str, str, list[str]]] = []

    labels: list[bool] = []
    chosen_scores: list[float] = []
    base_scores: list[float] = []
    per_query_rows: list[dict[str, Any]] = []
    by_canonical: dict[str, dict[str, list[bool]]] = defaultdict(
        lambda: {"expected": [], "predicted_at_best_threshold": []}
    )

    for index, test in enumerate(tests):
        prompt = test["prompt"]
        expected = bool(test["expected_match"])
        canonical_id = test["canonical_id"]

        pool = cache[-args.max_candidates :] if args.max_candidates else list(cache)
        candidates = [
            _build_candidate(prompt_text, entities)
            for prompt_text, _cid, entities in pool
        ]

        if not candidates:
            best_rerank = 0.0
            best_base = 0.0
            correct_present = False
            factor_means: dict[str, float] = {}
        else:
            request_entities = entity_extractor.get_qdrant_entity_tags(prompt)
            reranked = reranker.rerank(
                query=f"model:{DEFAULT_MODEL}|system: {DEFAULT_SYSTEM_PROMPT}|user: {prompt}",
                candidates=candidates,
                request_model=DEFAULT_MODEL,
                request_service=DEFAULT_SERVICE,
                request_intent=None,
                request_temperature=DEFAULT_TEMPERATURE,
                request_entities=request_entities,
                user_query=prompt,
                system_query=DEFAULT_SYSTEM_PROMPT,
            )
            top = reranked[0] if reranked else None
            best_rerank = top["rerank_score"] if top else 0.0
            best_base = top.get("base_normalized_score", 0.0) if top else 0.0
            factor_means = {}
            if top:
                for key in _FACTOR_KEYS:
                    factor_means[key] = top["score_breakdown"]["factors"].get(key, 1.0)
            correct_present = any(cid == canonical_id for _p, cid, _e in pool)

        chosen = best_rerank if args.score_source == "rerank" else best_base
        labels.append(expected)
        chosen_scores.append(chosen)
        base_scores.append(best_base)

        per_query_rows.append(
            {
                "index": index,
                "test_id": test.get("test_id", ""),
                "canonical_id": canonical_id,
                "expected_match": expected,
                "correct_canonical_in_pool": correct_present,
                "n_candidates": len(candidates),
                "best_rerank_score": round(best_rerank, 6),
                "best_base_score": round(best_base, 6),
                **{f"factor_{k}": round(v, 4) for k, v in factor_means.items()},
            }
        )

        # Advance the simulated cache after scoring so a prompt never matches itself.
        cache.append(
            (
                prompt,
                canonical_id,
                entity_extractor.get_qdrant_entity_tags(prompt),
            )
        )
        if index and index % 25 == 0:
            print(f"  ...{index}/{len(tests)}")

    sweep = _sweep(labels, chosen_scores)
    best = _best_threshold(sweep)

    # Recompute predictions at the recommended threshold for the per-canonical view.
    for row in per_query_rows:
        score = (
            row["best_rerank_score"]
            if args.score_source == "rerank"
            else row["best_base_score"]
        )
        row["predicted_at_best_threshold"] = score >= best["threshold"]
        by_canonical[row["canonical_id"]]["expected"].append(row["expected_match"])
        by_canonical[row["canonical_id"]]["predicted_at_best_threshold"].append(
            row["predicted_at_best_threshold"]
        )

    positives = [s for s, lab in zip(chosen_scores, labels, strict=False) if lab]
    negatives = [s for s, lab in zip(chosen_scores, labels, strict=False) if not lab]
    auc = _auc(positives, negatives)

    # Per-canonical recall at the recommended threshold (how often a should-match
    # group actually got accepted).
    canonical_recall: dict[str, dict[str, Any]] = {}
    for cid, counts in by_canonical.items():
        total = len(counts["expected"])
        matched = sum(
            1
            for exp, pred in zip(
                counts["expected"],
                counts["predicted_at_best_threshold"],
                strict=False,
            )
            if exp and pred
        )
        should = sum(1 for exp in counts["expected"] if exp)
        if should:
            canonical_recall[cid] = {
                "should_match": should,
                "accepted": matched,
                "recall": round(matched / should, 4),
                "total": total,
            }

    summary = {
        "scoring_mode": config.RERANKER_SCORING_MODE,
        "score_source": args.score_source,
        "queries": len(tests),
        "positives": len(positives),
        "negatives": len(negatives),
        "auc": round(auc, 4) if auc == auc else None,  # NaN guard
        "mean_positive_score": round(statistics.mean(positives), 4)
        if positives
        else None,
        "mean_negative_score": round(statistics.mean(negatives), 4)
        if negatives
        else None,
        "recommended_threshold": best["threshold"],
        "recommended_metrics": best,
        "canonical_recall": canonical_recall,
        "threshold_sweep": sweep,
    }

    fieldnames = [
        "index",
        "test_id",
        "canonical_id",
        "expected_match",
        "correct_canonical_in_pool",
        "n_candidates",
        "best_rerank_score",
        "best_base_score",
        "predicted_at_best_threshold",
        *[f"factor_{key}" for key in _FACTOR_KEYS],
    ]
    _write_outputs(args.output_prefix, summary, per_query_rows, fieldnames)

    _print_report(summary, best, sweep)
    print(f"\nWrote {args.output_prefix}.csv and {args.output_prefix}.json")


def _write_outputs(
    prefix: str,
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    fieldnames: list[str],
) -> None:
    with open(f"{prefix}.json", "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    with open(f"{prefix}.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _print_report(
    summary: dict[str, Any], best: dict[str, float], sweep: list[dict[str, float]]
) -> None:
    print("\n=== Reranker calibration ===")
    print(f"source:             {summary.get('source', 'static')}")
    print(f"samples:            {summary['queries']}")
    print(f"positives/negatives:{summary['positives']}/{summary['negatives']}")
    print(f"AUC:                {summary['auc']}")
    print(f"mean pos score:     {summary['mean_positive_score']}")
    print(f"mean neg score:     {summary['mean_negative_score']}")
    print(
        f"recommended thresh: {summary['recommended_threshold']} "
        f"(P={best['precision']}, R={best['recall']}, F1={best['f1']}, "
        f"accepted={best['accepted']})"
    )

    print("\nthreshold  precision  recall   f1     accepted")
    for row in sweep:
        if row["accepted"] == 0 and row["threshold"] > 0.0:
            continue
        print(
            f"{row['threshold']:>8.4f}  {row['precision']:>8.4f}  "
            f"{row['recall']:>6.4f}  {row['f1']:>5.3f}  {row['accepted']:>6}"
        )


def _load_live_audit_rows(
    intent: str | None = None,
    model: str | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Read compared ``cache_evaluations`` rows: rerank score + judge verdict.

    ``task_db_session`` puts the session in system RLS mode, which is required
    here: calibration has to see every tenant's audits, and a plain `SessionLocal`
    would only see the anonymous tenant. Rows are distilled inside the session
    so nothing detached leaks out.
    """
    from sqlalchemy import select

    from app.core.database import task_db_session
    from app.modules.cache_audit.cache_audit_model import CacheEvaluation

    with task_db_session() as session:
        stmt = select(CacheEvaluation).where(
            CacheEvaluation.rerank_score.isnot(None),
            CacheEvaluation.is_equivalent.isnot(None),
        )
        if intent:
            stmt = stmt.where(CacheEvaluation.intent == intent)
        if model:
            stmt = stmt.where(CacheEvaluation.model == model)
        stmt = stmt.order_by(CacheEvaluation.created_at.asc(), CacheEvaluation.id.asc())
        if limit:
            stmt = stmt.limit(limit)

        return [
            {
                "audit_id": row.id,
                "intent": row.intent,
                "model": row.model,
                "rerank_score": float(row.rerank_score),
                "vector_similarity": float(row.vector_similarity)
                if row.vector_similarity is not None
                else None,
                "is_equivalent": bool(row.is_equivalent),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in session.scalars(stmt).all()
        ]


def _per_intent_calibration(
    rows: list[dict[str, Any]],
    alpha: float,
    moderate_alpha: float,
    min_samples: int,
) -> dict[str, dict[str, Any]]:
    """Recommend a ''high''/''moderate'' threshold per intent from live audits.

    Uses the same conformal quantile rule as the scheduled calibration job
    (`IntentCalibrationService`), so this report predicts what
    `python -m app.scripts.calibrate_intent_thresholds` would apply.
    """
    from app.modules.cache_audit.calibration_service import (
        compute_conformal_threshold,
    )

    by_intent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_intent[row["intent"] or "unclassified"].append(row)

    results: dict[str, dict[str, Any]] = {}
    for intent_name, items in sorted(by_intent.items()):
        defaults = config.CACHE_CONFIDENCE_THRESHOLDS.get(
            intent_name,
            config.CACHE_CONFIDENCE_THRESHOLDS.get(
                "general_query", {"high": 0.95, "moderate": 0.88}
            ),
        )
        scores = [item["rerank_score"] for item in items]
        labels = [item["is_equivalent"] for item in items]
        errors = sum(1 for label in labels if not label)

        if len(items) < min_samples:
            results[intent_name] = {
                "samples": len(items),
                "errors": errors,
                "default_high": defaults["high"],
                "default_moderate": defaults["moderate"],
                "high": defaults["high"],
                "moderate": defaults["moderate"],
                "empirical_error_rate": 0.0,
                "error_count": errors,
                "calibrated": False,
                "reason": f"Insufficient samples ({len(items)} < {min_samples})",
            }
            continue

        high, high_error_rate, high_error_count = compute_conformal_threshold(
            scores=scores,
            is_equivalent=labels,
            alpha=alpha,
            default_threshold=defaults["high"],
        )
        moderate, _, _ = compute_conformal_threshold(
            scores=scores,
            is_equivalent=labels,
            alpha=moderate_alpha,
            default_threshold=defaults["moderate"],
        )

        results[intent_name] = {
            "samples": len(items),
            "errors": errors,
            "default_high": defaults["high"],
            "default_moderate": defaults["moderate"],
            "high": high,
            # Mirrors the service: moderate may never exceed high.
            "moderate": min(moderate, high),
            "empirical_error_rate": high_error_rate,
            "error_count": high_error_count,
            "calibrated": True,
            "reason": None,
        }
    return results


def _run_audit_calibration(args: argparse.Namespace) -> int:
    rows = _load_live_audit_rows(intent=args.intent, model=args.model, limit=args.limit)
    if not rows:
        print(
            "No compared audit records found in cache_evaluations "
            "(need rerank_score and is_equivalent). Run the shadow audit first, "
            "or pass --intent/--model if you expect a narrower slice.",
            file=sys.stderr,
        )
        return 1

    labels = [row["is_equivalent"] for row in rows]
    scores = [row["rerank_score"] for row in rows]

    sweep = _sweep(labels, scores)
    best = _best_threshold(sweep)

    positives = [score for score, label in zip(scores, labels, strict=False) if label]
    negatives = [
        score for score, label in zip(scores, labels, strict=False) if not label
    ]
    auc = _auc(positives, negatives)

    for row in rows:
        row["predicted_at_best_threshold"] = row["rerank_score"] >= best["threshold"]

    per_intent = _per_intent_calibration(
        rows,
        alpha=args.alpha,
        moderate_alpha=args.moderate_alpha,
        min_samples=args.min_samples,
    )

    summary = {
        "source": "audit",
        "intent_filter": args.intent,
        "model_filter": args.model,
        "queries": len(rows),
        "positives": len(positives),
        "negatives": len(negatives),
        "auc": round(auc, 4) if auc == auc else None,  # NaN guard
        "mean_positive_score": round(statistics.mean(positives), 4)
        if positives
        else None,
        "mean_negative_score": round(statistics.mean(negatives), 4)
        if negatives
        else None,
        "recommended_threshold": best["threshold"],
        "recommended_metrics": best,
        "alpha": args.alpha,
        "moderate_alpha": args.moderate_alpha,
        "min_samples": args.min_samples,
        "per_intent": per_intent,
        "threshold_sweep": sweep,
    }

    fieldnames = [
        "audit_id",
        "intent",
        "model",
        "rerank_score",
        "vector_similarity",
        "is_equivalent",
        "predicted_at_best_threshold",
        "created_at",
    ]
    _write_outputs(args.output_prefix, summary, rows, fieldnames)

    _print_report(summary, best, sweep)
    _print_per_intent(per_intent)
    print(
        "\nApply calibrated overrides with: "
        "poetry run python -m app.scripts.calibrate_intent_thresholds --apply"
    )
    print(f"\nWrote {args.output_prefix}.csv and {args.output_prefix}.json")
    return 0


def _print_per_intent(per_intent: dict[str, dict[str, Any]]) -> None:
    print("\n=== Per-intent conformal recommendation ===")
    print(
        f"{'INTENT':<22} | {'SAMPLES':<7} | {'ERR':<4} | "
        f"{'DEFAULT (H/M)':<15} | {'RECOMMENDED (H/M)':<18} | STATUS"
    )
    print("-" * 104)
    for intent, result in sorted(per_intent.items()):
        default_str = f"{result['default_high']:.2f} / {result['default_moderate']:.2f}"
        recommended = f"{result['high']:.4f} / {result['moderate']:.4f}"
        status = (
            f"err {result['empirical_error_rate'] * 100:.1f}%"
            if result["calibrated"]
            else f"SKIPPED ({result['reason']})"
        )
        print(
            f"{intent:<22} | {result['samples']:<7} | {result['errors']:<4} | "
            f"{default_str:<15} | {recommended:<18} | {status}"
        )


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.scoring_mode:
        config.RERANKER_SCORING_MODE = args.scoring_mode

    if args.source == "audit":
        return _run_audit_calibration(args)

    _run_static_calibration(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
