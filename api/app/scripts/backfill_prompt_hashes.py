"""Backfill ``llm_usage_logs.prompt_hash`` for rows written before the column.

The hash is a SHA-256 of the system prompt only, which lets the analytics
dashboard attribute cost per downstream application without caller tagging.
New rows are hashed at write time; this fills in history from ``input_text``.

Usage:
    PYTHON_ENV=local poetry run python -m app.scripts.backfill_prompt_hashes
    PYTHON_ENV=local poetry run python -m app.scripts.backfill_prompt_hashes \\
        --batch-size 200 [--dry-run]

Rows that cannot be recovered unambiguously (no system message, or an
``input_text`` whose encoding was mangled by message content) are skipped and
counted. They keep a NULL hash and appear as an unattributed bucket in the
analytics prompt breakdown.

Safe to re-run: it only touches rows whose ``prompt_hash`` is still NULL.
"""

import argparse

from app.core.config import config
from app.core.prompt_identity import system_prompt_from_prompt_text
from app.modules.llm_usage.llm_usage_model import LlmUsageLog
from app.modules.llm_usage.llm_usage_service import LlmUsageService


def _dry_run(batch_size: int) -> int:
    service = LlmUsageService()
    scanned = attributed = skipped = 0
    with service._get_read_session() as session:  # noqa: SLF001 - read-only probe
        for (input_text,) in (
            session.query(LlmUsageLog.input_text)
            .filter(
                LlmUsageLog.prompt_hash.is_(None),
                LlmUsageLog.input_text.isnot(None),
            )
            .yield_per(batch_size)
        ):
            scanned += 1
            if system_prompt_from_prompt_text(input_text) is None:
                skipped += 1
            else:
                attributed += 1
    print(
        f"Dry run: {scanned} row(s) with a NULL prompt_hash — "
        f"{attributed} attributable, {skipped} not recoverable."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill prompt_hash on llm_usage_logs from stored input_text."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows per transaction (default: 500).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    args = parser.parse_args()

    config.load()

    if args.dry_run:
        return _dry_run(args.batch_size)

    scanned, updated, skipped = LlmUsageService().backfill_prompt_hashes(
        args.batch_size
    )
    print(
        f"Scanned {scanned} row(s): {updated} attributed, {skipped} skipped "
        f"(no system prompt, or ambiguous input_text encoding)."
    )
    if updated:
        print(
            "Spot-check one hash with:\n"
            '  python -c "from app.core.prompt_identity import '
            "compute_prompt_hash; print(compute_prompt_hash('<your system prompt>'))\""
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
