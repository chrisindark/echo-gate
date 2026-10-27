#!/usr/bin/env python
"""Latency probe for whatever implements the contract.

    INFERENCE_BASE_URL=http://localhost:7997 python tools/bench.py
    INFERENCE_BASE_URL=http://inference:7997 python tools/bench.py --n 50

Medians, because inference latency is dominated by tail events (first call after
load, MPS kernel compilation, GC) that a mean would smear.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.request

BASE_URL = os.getenv("INFERENCE_BASE_URL", "http://localhost:7997").rstrip("/")


def call(path: str, payload: dict, runs: int) -> tuple[float, float, float]:
    timings = []
    for _ in range(runs):
        request = urllib.request.Request(
            f"{BASE_URL}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        start = time.perf_counter()
        with urllib.request.urlopen(request, timeout=120) as response:
            response.read()
        timings.append((time.perf_counter() - start) * 1000)
    timings.sort()
    return statistics.median(timings), timings[0], timings[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=15, help="runs per case")
    args = parser.parse_args()

    fixtures = {
        "embed x1": ("/embeddings", {"input": "how do I rotate an api key"}),
        "embed x8": (
            "/embeddings",
            {"input": [f"prompt number {i} about refunds and keys" for i in range(8)]},
        ),
        "embed x32": (
            "/embeddings",
            {"input": [f"prompt number {i} about refunds and keys" for i in range(32)]},
        ),
        "rerank x3": (
            "/rerank",
            {"query": "what is the refund window", "documents": ["a", "b", "c"]},
        ),
        "rerank x16": (
            "/rerank",
            {
                "query": "what is the refund window",
                "documents": [f"candidate document {i}" for i in range(16)],
            },
        ),
        "classify x1": (
            "/classify",
            {"pairs": [["what is the refund window", "refunds within 30 days"]]},
        ),
        "classify x8": (
            "/classify",
            {"pairs": [["what is the refund window", f"answer {i}"] for i in range(8)]},
        ),
    }

    # Warm up so the first case does not absorb one-off device/allocator setup.
    for path, payload in fixtures.values():
        call(path, payload, 1)

    print(f"target: {BASE_URL}  (n={args.n}, ms)")
    print(f"{'case':14s} {'median':>8s} {'min':>8s} {'max':>8s}")
    for label, (path, payload) in fixtures.items():
        median, low, high = call(path, payload, args.n)
        print(f"{label:14s} {median:8.2f} {low:8.2f} {high:8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
