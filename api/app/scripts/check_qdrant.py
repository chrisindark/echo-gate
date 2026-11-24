"""Check the first few points in the configured Qdrant collection."""

import asyncio

from app.core.config import config
from app.core.env import load_app_env

load_app_env()

from qdrant_client import AsyncQdrantClient


async def main():
    client = AsyncQdrantClient(
        url=config.QDRANT_URL,
        api_key=config.QDRANT_API_KEY or None,
    )
    collection = config.QDRANT_COLLECTION_NAME
    print(f"Connecting to Qdrant at {config.QDRANT_URL}, collection: {collection}")
    try:
        response = await client.scroll(
            collection_name=collection, limit=5, with_payload=True
        )
        points = response[0]
        if not points:
            print("Collection is empty.")
        for point in points:
            print(f"ID: {point.id}")
            print(f"Scope: {point.payload.get('scope')}")
            print(f"Tenant ID: {point.payload.get('tenant_id')}")
            print(f"User ID: {point.payload.get('user_id')}")
            print("-" * 20)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
