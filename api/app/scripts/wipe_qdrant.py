"""Safely drop a Qdrant collection.

Requires explicit confirmation (--confirm) to prevent accidental data loss in
staging, production, or remote clusters.

Usage:
    poetry run python -m app.scripts.wipe_qdrant --confirm
    poetry run python -m app.scripts.wipe_qdrant --collection custom_coll --confirm
"""

import argparse
import asyncio
import sys

from app.core.config import config
from app.core.env import load_app_env

load_app_env()

from qdrant_client import AsyncQdrantClient


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely drop a Qdrant collection.")
    parser.add_argument(
        "--collection",
        default=config.QDRANT_COLLECTION_NAME,
        help="Target collection to delete (default: config.QDRANT_COLLECTION_NAME)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Bypass interactive confirmation prompt (required for non-interactive use).",
    )
    args = parser.parse_args(argv)

    target_url = config.QDRANT_URL
    target_coll = args.collection

    print(f"Target Qdrant URL: {target_url}")
    print(f"Target Collection: {target_coll}")

    if not args.confirm:
        print(
            f"WARNING: This will permanently delete collection '{target_coll}' on '{target_url}'!",
            file=sys.stderr,
        )
        try:
            answer = (
                input(f"Are you sure you want to delete '{target_coll}'? [y/N]: ")
                .strip()
                .lower()
            )
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.", file=sys.stderr)
            return 1

        if answer != "y":
            print("Aborted. Pass --confirm to bypass prompt.", file=sys.stderr)
            return 1

    client = AsyncQdrantClient(
        url=target_url,
        api_key=config.QDRANT_API_KEY or None,
    )
    try:
        await client.delete_collection(target_coll)
        print(f"Successfully dropped collection '{target_coll}' on {target_url}.")
        return 0
    except Exception as e:
        print(f"Failed to drop collection '{target_coll}': {e}", file=sys.stderr)
        return 2
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
