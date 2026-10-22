"""Read-only report over semantic-cache reranker decisions from the trace table.

The offline `calibrate_reranker` sweep cannot exercise the LLM-extracted metadata
or the intent thresholds, so the only place to see those factors is real traffic.
Every semantic lookup now persists `score_breakdown` on its `gateway_requests`
row (accepted hits and rejected near-misses alike), which makes this report
possible.

It highlights the acceptance boundary that the metadata penalty can straddle:
a near-identical prompt whose base cross-encoder score is above the intent's
high threshold but whose bounded factors drag the final score just below it.

Usage:
    PYTHON_ENV=local poetry run python -m app.scripts.inspect_semantic_cache
    poetry run python -m app.scripts.inspect_semantic_cache --limit 500
    poetry run python -m app.scripts.inspect_semantic_cache --json > report.json
"""

import argparse
import json
from typing import Any

from sqlalchemy import select

from app.core.config import config
from app.core.database import task_db_session
from app.modules.gateway_requests.gateway_requests_model import GatewayRequestLog
from app.modules.gateway_requests.gateway_requests_schema import RoutingDecision

_NOISY_FACTORS = ("metadata", "system_prompt", "temperature", "instruction")


def _high_threshold(intent: str | None) -> float:
    entry = config.CACHE_CONFIDENCE_THRESHOLDS.get(intent or "", {})
    return float(entry.get("high", 0.95))


def _factor_drags(breakdown: dict[str, Any], factor: str) -> float:
    return float(breakdown.get("factors", {}).get(factor, 1.0))


def _summarize(row: GatewayRequestLog) -> dict[str, Any] | None:
    breakdown = row.score_breakdown
    if not isinstance(breakdown, dict) or "base_score" not in breakdown:
        return None

    high = _high_threshold(row.intent)
    base = float(breakdown["base_score"])
    final = float(breakdown["final_score"])
    accepted = row.routing_decision == RoutingDecision.QDRANT_SEMANTIC.value

    drags = {
        factor: _factor_drags(breakdown, factor)
        for factor in _NOISY_FACTORS
        if _factor_drags(breakdown, factor) < 1.0
    }

    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "routing_decision": row.routing_decision,
        "intent": row.intent,
        "accepted": accepted,
        "high_threshold": high,
        "base_score": round(base, 6),
        "final_score": round(final, 6),
        # Base cleared the threshold but the composite dropped below it: the
        # candidate is the class most likely to be a false rejection.
        "below_threshold_despite_base": (not accepted)
        and base >= high
        and final < high,
        # Rejected by a razor-thin margin against the high threshold.
        "boundary_margin": round(final - high, 6) if not accepted else None,
        "factor_drags": drags,
        "metadata_mismatches": breakdown.get("details", {}).get(
            "metadata_mismatches", []
        ),
        "metadata_comparisons": breakdown.get("details", {}).get(
            "metadata_comparisons"
        ),
        "query_text": (row.query_text or "")[:300],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=200, help="Most recent rows to scan."
    )
    parser.add_argument(
        "--boundary-margin",
        type=float,
        default=0.02,
        help="Report rejections within this distance below the high threshold.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of text."
    )
    args = parser.parse_args()

    with task_db_session() as session:
        rows = (
            session.execute(
                select(GatewayRequestLog)
                .where(GatewayRequestLog.score_breakdown.is_not(None))
                .order_by(GatewayRequestLog.id.desc())
                .limit(args.limit)
            )
            .scalars()
            .all()
        )

    summaries = [s for s in (_summarize(r) for r in rows) if s is not None]
    hits = [s for s in summaries if s["accepted"]]
    misses = [s for s in summaries if not s["accepted"]]
    false_rejections = [s for s in misses if s["below_threshold_despite_base"]]
    boundary = [
        s
        for s in misses
        if s["boundary_margin"] is not None
        and -args.boundary_margin <= s["boundary_margin"] < 0
    ]

    drag_counts: dict[str, int] = {}
    for summary in misses:
        for factor in summary["factor_drags"]:
            drag_counts[factor] = drag_counts.get(factor, 0) + 1

    report = {
        "rows_scanned": len(rows),
        "semantic_decisions": len(summaries),
        "accepted": len(hits),
        "rejected": len(misses),
        "rejected_despite_base_above_threshold": len(false_rejections),
        "rejected_within_boundary_margin": len(boundary),
        "rejection_drag_counts": drag_counts,
        "false_rejections": false_rejections,
        "boundary_rejections": boundary,
    }

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return

    print("=== Semantic cache reranker report ===")
    print(f"rows scanned:                         {report['rows_scanned']}")
    print(f"semantic decisions (with breakdown):  {report['semantic_decisions']}")
    print(f"accepted (QDRANT_SEMANTIC):           {report['accepted']}")
    print(f"rejected:                             {report['rejected']}")
    print(
        "rejected despite base >= threshold:   "
        f"{report['rejected_despite_base_above_threshold']}"
    )
    print(
        "rejected within boundary margin:      "
        f"{report['rejected_within_boundary_margin']}"
    )
    if drag_counts:
        print(f"rejection drags by factor:            {drag_counts}")

    def _dump(title: str, items: list[dict[str, Any]]) -> None:
        print(f"\n--- {title} ---")
        if not items:
            print("(none)")
            return
        for s in items:
            print(
                f"#{s['id']} {s['routing_decision']} intent={s['intent']} "
                f"base={s['base_score']} final={s['final_score']} "
                f"high={s['high_threshold']} drags={s['factor_drags']}"
            )
            print(f"    mismatches={s['metadata_mismatches']}")
            print(f"    query={s['query_text']!r}")

    _dump(
        "Rejections with base >= high threshold (candidate false rejections)",
        false_rejections,
    )
    _dump("Rejections within boundary margin", boundary)


if __name__ == "__main__":
    main()
