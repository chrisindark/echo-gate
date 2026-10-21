import asyncio

from qdrant_client import AsyncQdrantClient


async def main():
    client = AsyncQdrantClient(host="localhost", port=6333)
    await client.delete_collection("embeddings_multivector")
    print("Dropped embeddings_multivector")


asyncio.run(main())
