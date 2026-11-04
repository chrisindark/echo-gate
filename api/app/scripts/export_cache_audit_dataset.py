"""Export in-domain cache audit dataset for reranker fine-tuning.

Exports (query_text, cached_answer, is_equivalent) triples from cache_evaluations
table as they accumulate. Useful for fine-tuning an in-domain cross-encoder reranker.

Usage:
    poetry run python -m app.scripts.export_cache_audit_dataset
    poetry run python -m app.scripts.export_cache_audit_dataset --output data/cache_audit.jsonl
    poetry run python -m app.scripts.export_cache_audit_dataset --include-uncompared
    poetry run python -m app.scripts.export_cache_audit_dataset --format jsonl --output data/cache_audit.jsonl
    poetry run python -m app.scripts.export_cache_audit_dataset --format csv
"""

import argparse
import csv
import json
import os
from datetime import datetime

from app.modules.cache_audit.cache_audit_service import CacheAuditService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export in-domain cache audit dataset for reranker fine-tuning"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output file path (default: cache_audit_dataset_<timestamp>.<ext>)",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["json", "jsonl", "csv"],
        default="jsonl",
        help="Output format (default: jsonl)",
    )
    parser.add_argument(
        "--include-uncompared",
        action="store_true",
        help="Include audits with is_equivalent=None (uncompared)",
    )
    parser.add_argument(
        "--tenant-id", type=str, default=None, help="Filter by tenant_id"
    )
    parser.add_argument("--model", type=str, default=None, help="Filter by model")
    parser.add_argument("--intent", type=str, default=None, help="Filter by intent")
    args = parser.parse_args()

    service = CacheAuditService()
    rows = service.export_in_domain_dataset(
        tenant_id=args.tenant_id,
        model=args.model,
        intent=args.intent,
        include_uncompared=args.include_uncompared,
    )

    if not rows:
        print("No audit records found to export.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output:
        output_path = args.output
    else:
        ext = "jsonl" if args.format == "jsonl" else args.format
        output_path = f"cache_audit_dataset_{timestamp}.{ext}"

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    if args.format == "jsonl":
        with open(output_path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    elif args.format == "json":
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
    elif args.format == "csv":
        fieldnames = [
            "query_text",
            "cached_answer",
            "is_equivalent",
            "intent",
            "model",
            "request_id",
            "exact_hash",
        ]
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k) for k in fieldnames})

    print(f"Exported {len(rows)} audit records to {output_path}")
    print(f"  Format: {args.format}")
    print(f"  Compared only: {not args.include_uncompared}")


if __name__ == "__main__":
    main()
