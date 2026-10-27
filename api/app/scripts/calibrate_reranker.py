"""Offline calibration sweep for the semantic-cache reranker.

This script replays the labelled `TEST_DATASET` through the real
`RerankerService` (cross-encoder + bounded signal factors) *without* needing
Redis, Qdrant, the API, or the intent LLM. It simulates the sequential cache
that `run_tests.py` produces (every earlier prompt is a candidate for the
current prompt) and measures how well the composite `rerank_score` separates
expected matches from non-matches.

It then sweeps acceptance thresholds and reports precision/recall/F1 so the
`CACHE_CONFIDENCE_THRESHOLDS` values can be tuned from data, and writes:

  * ``reranker_calibration.csv``  - one row per test query
  * ``reranker_calibration.json`` - summary + recommended threshold

Caveat: intent classification and the LLM-extracted metadata (core_operation,
core_subject, subject_modifier, action_modifier) are unavailable offline, so
those factors are inert here (the request intent is ``None`` and the request
metadata is empty, both of which are deliberately *not* penalized). The sweep
therefore calibrates the dominant cross-encoder + entity signal; run it against
a live gateway trace to calibrate the metadata factors too.

Usage:
    poetry run python -m app.scripts.calibrate_reranker
    poetry run python -m app.scripts.calibrate_reranker --limit 60 --max-candidates 40
    poetry run python -m app.scripts.calibrate_reranker --scoring-mode additive
    poetry run python -m app.scripts.calibrate_reranker --score-source base
"""

import argparse
import csv
import json
import statistics
from collections import defaultdict
from typing import Any

from app.core.config import config
from app.modules.intent_classifier.entity_extractor_service import (
    EntityExtractorService,
)
from app.modules.reranking.reranker_service import RerankerService
from app.modules.verifier.instruction_service import InstructionVerifier
from app.scripts.test_dataset import TEST_DATASET

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


class _CrossEncoderAdapter:
    """Minimal `rerank_predict`-compatible wrapper around one CrossEncoder.

    Avoids loading the relevance/NLI models that `CrossEncoderService` also
    builds, which keeps calibration fast and dependency-light.
    """

    def __init__(self, model_name: str, cache_folder: str) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - deployment shape
            raise SystemExit(
                "This script needs the in-process cross-encoder. Install the "
                "local-models extra: poetry install --extras local-models"
            ) from exc

        self._model = CrossEncoder(
            model_name, local_files_only=True, cache_folder=cache_folder
        )

    def rerank_predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        scores = self._model.predict(pairs, show_progress_bar=False)
        if hasattr(scores, "ndim") and scores.ndim == 0:
            return [float(scores)]
        return [float(score) for score in scores]


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=0, help="Limit number of test cases (0 = all)."
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
        help="Override RERANKER_SCORING_MODE for this run.",
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
        "--model-name", default=config.RERANKER_MODEL_NAME, help="Cross-encoder model."
    )
    parser.add_argument(
        "--hf-home", default="./.model_cache", help="Local model cache directory."
    )
    args = parser.parse_args()

    if args.scoring_mode:
        config.RERANKER_SCORING_MODE = args.scoring_mode

    tests = TEST_DATASET[: args.limit] if args.limit else TEST_DATASET
    print(
        f"Calibrating {len(tests)} queries | scoring_mode={config.RERANKER_SCORING_MODE} "
        f"| score_source={args.score_source}"
    )

    cross_encoder = _CrossEncoderAdapter(args.model_name, args.hf_home)
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

    with open(f"{args.output_prefix}.json", "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

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
    with open(f"{args.output_prefix}.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(per_query_rows)

    print("\n=== Reranker calibration ===")
    print(f"queries:            {summary['queries']}")
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

    print(f"\nWrote {args.output_prefix}.csv and {args.output_prefix}.json")


if __name__ == "__main__":
    main()
