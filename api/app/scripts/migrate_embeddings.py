"""Re-embed a cache collection after an embedding model change.

Dense vectors are model-specific. A collection written by `all-MiniLM-L6-v2` is
not comparable with queries embedded by `bge-small-en-v1.5`, even though both emit
384 dimensions: the vectors live in different spaces, so cosine similarity between
the two is noise. `INFERENCE_EMBEDDING_DIM` guards the *dimension* mismatch and
`InferenceClient.verify()` guards the model id, but neither can repair data that is
already stored -- this script does.

Payloads carry the source text (`prompt`, `system_prompt`, `user_prompt`), so the
corpus survives the swap instead of being thrown away:

    # plan: count, dimensions, target schema. Writes nothing.
    poetry run python -m app.scripts.migrate_embeddings --target embeddings_multivector_v2

    # create the target and fill it
    poetry run python -m app.scripts.migrate_embeddings \
        --target embeddings_multivector_v2 --write

    # swap collections afterwards
    #   QDRANT_COLLECTION_NAME=embeddings_multivector_v2 (or the service default)

Sparse BM25 vectors are copied from the source untouched: BM25 is coupled to the
tokenizer, not to the dense model, so a dense swap does not invalidate them. The
target is created through `LlmCacheCollectionService` so its schema cannot drift
from what the gateway writes at request time.
"""

from __future__ import annotations

import argparse
import logging
import sys

from qdrant_client.http import models

from app.core.config import config
from app.modules.embedding.embedding_service import EmbeddingService
from app.modules.inference.inference_client import InferenceError
from app.modules.qdrant.llm_cache_collection_service import LlmCacheCollectionService
from app.modules.qdrant.qdrant_client_service import QdrantClientService

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_embeddings")

# Payload field holding the text that produced each named dense vector. `prompt`
# is the full serialized prompt (`model:...|system:...|user:...`), which is what
# `prompt_embedding` is built from at request time.
DENSE_FIELDS = {
    "prompt": "prompt_embedding",
    "system_prompt": "system_prompt_embedding",
    "user_prompt": "user_prompt_embedding",
}

# Copied verbatim: the tokenizer, not the dense model, defines this space.
SPARSE_VECTORS = ("system_prompt_bm25", "user_prompt_bm25")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="embeddings_multivector",
        help="collection to read (default: embeddings_multivector)",
    )
    parser.add_argument(
        "--target",
        default="embeddings_multivector_v2",
        help="collection to create and fill (default: embeddings_multivector_v2)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="actually create and fill the target; without it the run is a plan",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=128,
        help="points per scroll/embed/upsert batch (default: 128)",
    )
    return parser.parse_args(argv)


def _text(payload: dict, field: str) -> str:
    value = payload.get(field)
    return value if isinstance(value, str) else ""


def build_point(
    source_point: models.Record,
    vectors: dict[str, list[float]],
    embedding_model: str,
    embedding_version: str,
) -> models.PointStruct:
    """Same id, same payload, new dense vectors, sparse untouched."""
    payload = dict(source_point.payload or {})
    payload["embedding_model"] = embedding_model
    payload["embedding_version"] = embedding_version

    point_vectors: dict[str, object] = {}
    for field, vector_name in DENSE_FIELDS.items():
        vector = vectors.get(field)
        if vector:
            point_vectors[vector_name] = vector

    source_vectors = source_point.vector or {}
    if isinstance(source_vectors, dict):
        for name in SPARSE_VECTORS:
            if name in source_vectors:
                point_vectors[name] = source_vectors[name]

    return models.PointStruct(
        id=source_point.id, vector=point_vectors, payload=payload
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.source == args.target:
        logger.error("--source and --target must differ; re-embedding in place would")
        logger.error("overwrite the vectors it is still reading. Use a new collection.")
        return 2

    client_service = QdrantClientService()
    if not client_service.collection_exists(args.source):
        existing = [c.name for c in client_service.client.get_collections().collections]
        logger.error(
            "source collection %r does not exist; available: %s",
            args.source,
            existing or "none",
        )
        return 1

    try:
        embedding_service = EmbeddingService()
    except InferenceError as exc:
        logger.error("cannot re-embed without the inference engine: %s", exc)
        return 1

    probe = embedding_service.get_embedding("dimension probe")
    dim = len(probe)
    if dim != config.INFERENCE_EMBEDDING_DIM:
        logger.error(
            "engine emits dim=%s but INFERENCE_EMBEDDING_DIM=%s; a collection built "
            "from these vectors would not match the gateway's expectations",
            dim,
            config.INFERENCE_EMBEDDING_DIM,
        )
        return 1

    # The engine is the truth for the model id; config carries the same value
    # because `verify()` refuses to boot when the two disagree.
    embedding_model = config.INFERENCE_EMBED_MODEL or config.EMBEDDING_MODEL_NAME
    embedding_version = config.EMBEDDING_VERSION
    source_count = client_service.client.count(args.source, exact=True).count

    logger.info("source:      %s (%d points)", args.source, source_count)
    logger.info("target:      %s", args.target)
    logger.info("dense model: %s (dim=%d, version=%s)", embedding_model, dim, embedding_version)
    logger.info("sparse:      copied unchanged (%s)", ", ".join(SPARSE_VECTORS))

    if not args.write:
        logger.info("plan only; re-run with --write to create and fill the target")
        return 0

    # Creates the collection with the gateway's schema (named dense vectors,
    # sparse vectors, payload indexes, quantization) and validates the dimension.
    collection = LlmCacheCollectionService(
        client_service=client_service,
        collection_name=args.target,
        vector_size=dim,
    )

    processed = 0
    offset = None
    while True:
        points, offset = client_service.scroll(
            collection_name=args.source,
            limit=args.batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        if not points:
            break

        # One batched engine call per dense field instead of one per point: a
        # round trip per vector would make a large collection take hours.
        texts_by_field: dict[str, list[str]] = {}
        for point in points:
            payload = point.payload or {}
            for field in DENSE_FIELDS:
                text = _text(payload, field)
                if text:
                    texts_by_field.setdefault(field, []).append(text)

        embedded: dict[str, list[list[float]]] = {}
        for field, texts in texts_by_field.items():
            embedded[field] = embedding_service.inference.embed_sync(texts)

        cursors = dict.fromkeys(texts_by_field, 0)
        new_points = []
        for point in points:
            payload = point.payload or {}
            vectors: dict[str, list[float]] = {}
            for field, vector_name in DENSE_FIELDS.items():
                if field not in embedded:
                    continue
                if not _text(payload, field):
                    continue
                index = cursors[field]
                cursors[field] = index + 1
                vectors[field] = embedded[field][index]
            new_points.append(
                build_point(point, vectors, embedding_model, embedding_version)
            )

        collection.client_service.upsert(
            collection_name=args.target, points=new_points, wait=True
        )
        processed += len(new_points)
        logger.info("re-embedded %d/%d points", processed, source_count)

        if offset is None:
            break

    logger.info(
        "done: %d points in %s. Point the gateway at it (QDRANT collection name) "
        "before dropping %s.",
        processed,
        args.target,
        args.source,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
