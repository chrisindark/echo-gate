import asyncio

from qdrant_client import AsyncQdrantClient


async def main():
    client = AsyncQdrantClient(host="localhost", port=6333)
    response = await client.scroll(
        collection_name="embeddings_multivector", limit=5, with_payload=True
    )
    for point in response[0]:
        print(f"ID: {point.id}")
        print(f"Scope: {point.payload.get('scope')}")
        print(f"Tenant ID: {point.payload.get('tenant_id')}")
        print(f"User ID: {point.payload.get('user_id')}")
        print("-" * 20)


asyncio.run(main())
