#!/usr/bin/env python
"""Drift guard for the inference contract.

Runs a small set of known-good cases against whatever is listening on
``INFERENCE_BASE_URL`` and asserts shape, ordering, and semantic sanity. The
point is not accuracy benchmarking — it is catching the failure that a network
boundary makes invisible: a swapped model, an inverted label order, or a changed
score scale, all of which look like "the cache hit rate dropped" downstream.

    python evals/check.py
    uv run python evals/check.py
    INFERENCE_BASE_URL=http://localhost:7997 uv run python evals/check.py

The sparse check runs only when `/health` reports a loaded `sparse` role, so the
same script is valid against engines that do not serve one (Infinity).

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
    status, body = request_json(path, payload)
    if status >= 400:
        raise SystemExit(f"{path} -> HTTP {status}: {json.dumps(body)[:400]}")
    return body


def request_json(path: str, payload: dict) -> tuple[int, dict]:
    """POST and return the status even on an error, for capability probing."""
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(detail)
        except ValueError:
            return exc.code, {"raw": detail}
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


def _sparse_terms(entries: list[dict], index: int) -> set[int]:
    rows = [e for e in entries if e.get("index") == index]
    return set(rows[0].get("indices") or []) if rows else set()


def check_sparse() -> list[str]:
    """Sparse shape + determinism, only run when the sparse role is loaded.

    Infinity does not serve a sparse role, so this is skipped rather than failed
    against a third-party engine.
    """
    failures: list[str] = []
    probe = ["how do i rotate an api key", "rotating api keys for a service"]
    cold = post("/embeddings", {"input": probe, "dense": False, "sparse": True})
    again = post("/embeddings", {"input": probe, "dense": False, "sparse": True})

    entries = cold.get("sparse") or []
    if len(entries) != len(probe):
        return [f"expected {len(probe)} sparse rows, got {len(entries)}"]
    if "data" in cold:
        failures.append("dense=false still returned `data`")
    if not cold.get("sparse_model"):
        failures.append("response is missing `sparse_model`")

    for entry in entries:
        indices = entry.get("indices") or []
        values = entry.get("values") or []
        if not indices or not values:
            failures.append(f"row {entry.get('index')} is empty: {entry}")
        elif len(indices) != len(values):
            failures.append(
                f"row {entry.get('index')}: {len(indices)} indices vs "
                f"{len(values)} values"
            )
        elif any(v <= 0 for v in values):
            failures.append(f"row {entry.get('index')}: non-positive BM25 weight")

    if [(e.get("indices"), e.get("values")) for e in entries] != [
        (e.get("indices"), e.get("values")) for e in again.get("sparse") or []
    ]:
        failures.append("sparse output is not deterministic across identical calls")

    unrelated = post(
        "/embeddings",
        {"input": ["the cafeteria closes at five"], "dense": False, "sparse": True},
    )
    related_overlap = _sparse_terms(entries, 0) & _sparse_terms(entries, 1)
    unrelated_overlap = _sparse_terms(entries, 0) & _sparse_terms(
        unrelated.get("sparse") or [], 0
    )
    if len(related_overlap) <= len(unrelated_overlap):
        failures.append(
            "related texts share no more terms than unrelated ones "
            f"({len(related_overlap)} vs {len(unrelated_overlap)}) — tokenizer "
            "or weighting changed"
        )
    return failures


ZERO_SHOT_CASE = {
    "text": (
        "Please find attached the invoice for last month's subscription; "
        "payment is due within 30 days."
    ),
    "labels": ["billing", "password reset", "shipping"],
    "expect_label": "billing",
    "expect_min_score": 0.5,
}


def check_zero_shot() -> list[str] | None:
    """Zero-shot plumbing, or `None` when the engine does not implement the mode.

    The assertion is deliberately a *topic* case, which this NLI model does well
    (entailment 0.95). It is not an accuracy benchmark: the same model scores
    "phishing" emails as "account notice", so intent-style labels would make this
    guard flaky for reasons that have nothing to do with the contract.
    """
    status, body = request_json(
        "/classify",
        {
            "mode": "zero-shot",
            "inputs": [ZERO_SHOT_CASE["text"]],
            "labels": ZERO_SHOT_CASE["labels"],
        },
    )
    if status in (400, 404, 405, 422, 501):
        return None
    if status != 200:
        return [f"HTTP {status}: {json.dumps(body)[:300]}"]

    failures: list[str] = []
    if body.get("mode") != "zero-shot":
        failures.append(f"mode echoed as {body.get('mode')!r}")
    if body.get("entailment_label") != "entailment":
        failures.append(f"entailment_label is {body.get('entailment_label')!r}")

    rows = body.get("results") or []
    if len(rows) != 1:
        return failures + [f"expected 1 result row, got {len(rows)}"]
    row = rows[0]

    ranked = row.get("ranked") or []
    scores = [item["score"] for item in ranked]
    if scores != sorted(scores, reverse=True):
        failures.append(f"ranked not sorted by score: {scores}")
    if row.get("label") != ranked[0]["label"]:
        failures.append("`label` is not the top-ranked entry")
    if ranked[0]["label"] != ZERO_SHOT_CASE["expect_label"]:
        failures.append(
            f"top label {ranked[0]['label']!r} != expected "
            f"{ZERO_SHOT_CASE['expect_label']!r} — zero-shot sanity regressed "
            f"({[(r['label'], round(r['score'], 3)) for r in ranked]})"
        )
    if row.get("score", 0.0) < ZERO_SHOT_CASE["expect_min_score"]:
        failures.append(
            f"top score {row.get('score'):.3f} < "
            f"{ZERO_SHOT_CASE['expect_min_score']} "
            "(label phrasing or template changed?)"
        )
    if abs(sum((row.get("scores_normalized") or {}).values()) - 1.0) > 1e-3:
        failures.append("scores_normalized does not sum to 1")
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
    loaded = set((json.loads(health.read()).get("roles") or {}).keys())

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

    if "sparse" in loaded:
        sparse_failures = check_sparse()
        if sparse_failures:
            failed += 1
            print("FAIL  sparse")
            for failure in sparse_failures:
                print(f"        {failure}")
        else:
            print("ok    sparse")
    else:
        print("skip  sparse (role not loaded on this engine)")

    total = len(cases) + (1 if "sparse" in loaded else 0)
    zero_shot_failures = check_zero_shot()
    if zero_shot_failures is None:
        print("skip  zero-shot (engine does not implement mode=zero-shot)")
    elif zero_shot_failures:
        failed += 1
        total += 1
        print("FAIL  zero-shot")
        for failure in zero_shot_failures:
            print(f"        {failure}")
    else:
        total += 1
        print("ok    zero-shot")

    print(f"\n{total - failed}/{total} passed against {BASE_URL}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
