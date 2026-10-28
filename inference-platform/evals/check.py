#!/usr/bin/env python
"""Drift guard for the inference contract.

Runs a small set of known-good cases against whatever is listening on
``INFERENCE_BASE_URL`` and asserts shape, ordering, and semantic sanity. The
point is not accuracy benchmarking — it is catching the failure that a network
boundary makes invisible: a swapped model, an inverted label order, or a changed
score scale, all of which look like "the cache hit rate dropped" downstream.

    python evals/check.py
    INFERENCE_BASE_URL=http://localhost:7997 python evals/check.py

Exits non-zero on the first failing case group.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.getenv("INFERENCE_BASE_URL", "http://localhost:7997").rstrip("/")
CASES = Path(__file__).resolve().parent / "golden_pairs.jsonl"


def post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"{path} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"cannot reach {BASE_URL}{path}: {exc.reason}\n"
            "is the server running? (dev/serve.sh, or the infinity compose service)"
        ) from exc


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def check_rerank(case: dict) -> list[str]:
    body = post("/rerank", {"query": case["query"], "documents": case["documents"]})
    results = body.get("results") or []
    failures: list[str] = []

    if len(results) != len(case["documents"]):
        failures.append(
            f"expected {len(case['documents'])} results, got {len(results)}"
        )

    scores = [r["score"] for r in results]
    if any(not 0.0 <= s <= 1.0 for s in scores):
        failures.append(f"scores outside [0,1]: {scores}")
    if scores != sorted(scores, reverse=True):
        failures.append(f"results not sorted by score: {scores}")

    ranking = [r["index"] for r in results]
    if ranking != case["expect_ranking"]:
        failures.append(f"ranking {ranking} != expected {case['expect_ranking']}")

    spread = case.get("expect_min_spread")
    if spread is not None and scores and (scores[0] - scores[-1]) < spread:
        failures.append(
            f"score spread {scores[0] - scores[-1]:.3f} < {spread} — "
            "model may be flat/broken or the scale changed"
        )
    return failures


def check_classify(case: dict) -> list[str]:
    body = post("/classify", {"pairs": [[case["premise"], case["hypothesis"]]]})
    failures: list[str] = []

    labels = body.get("labels") or []
    if "entailment" not in labels or "contradiction" not in labels:
        failures.append(f"missing NLI labels, got {labels}")
        return failures

    result = (body.get("results") or [{}])[0]
    probs = result.get("probabilities") or {}
    if abs(sum(probs.values()) - 1.0) > 1e-3:
        failures.append(f"probabilities do not sum to 1: {probs}")

    predicted = result.get("label")
    if predicted != case["expect_label"]:
        failures.append(f"label {predicted!r} != expected {case['expect_label']!r}")
    return failures


def check_embeddings(case: dict) -> list[str]:
    body = post("/embeddings", {"input": case["texts"]})
    data = body.get("data") or []
    failures: list[str] = []

    if len(data) != len(case["texts"]):
        failures.append(f"expected {len(case['texts'])} vectors, got {len(data)}")
        return failures

    similarity = cosine(data[0]["embedding"], data[1]["embedding"])
    low = case.get("expect_min_cosine")
    high = case.get("expect_max_cosine")
    if low is not None and similarity < low:
        failures.append(f"cosine {similarity:.3f} < {low} (paraphrase not close)")
    if high is not None and similarity > high:
        failures.append(f"cosine {similarity:.3f} > {high} (unrelated text too close)")
    return failures


HANDLERS = {
    "rerank": check_rerank,
    "classify": check_classify,
    "embeddings": check_embeddings,
}


def main() -> int:
    health = urllib.request.urlopen(f"{BASE_URL}/health", timeout=10)
    if health.status != 200:
        raise SystemExit(f"health returned {health.status}")

    cases = [
        json.loads(line) for line in CASES.read_text().splitlines() if line.strip()
    ]
    failed = 0
    for case in cases:
        handler = HANDLERS[case["endpoint"]]
        failures = handler(case)
        if failures:
            failed += 1
            print(f"FAIL  {case['id']}")
            for failure in failures:
                print(f"        {failure}")
        else:
            print(f"ok    {case['id']}")

    print(f"\n{len(cases) - failed}/{len(cases)} passed against {BASE_URL}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
