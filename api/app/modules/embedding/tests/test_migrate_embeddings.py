"""Tests for the embedding migration script's selection and limits.

The script is an operator tool, so these tests pin the parts that decide *what*
gets migrated — dry-run, the high-value threshold, `--limit`, and default
naming — against an in-memory Qdrant/embedding stand-in. A live run still needs
a real Qdrant collection and a running inference engine.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.core.config import config
from app.scripts import migrate_embeddings


def _point(point_id: str, retention_value: float | None = 1.0):
    payload = {
        "prompt": f"model:gpt-4o-mini|user: {point_id}",
        "user_prompt": point_id,
        "response": {"choices": []},
    }
    if retention_value is not None:
        payload["retention_value"] = retention_value
    return SimpleNamespace(
        id=point_id,
        payload=payload,
        vector={
            "prompt_embedding": [0.9, 0.9, 0.9],
            "system_prompt_bm25": {"indices": [1], "values": [1.0]},
            "user_prompt_bm25": {"indices": [2], "values": [0.5]},
        },
    )


def _threshold(scroll_filter):
    if scroll_filter is None:
        return None
    return scroll_filter.must[0].range.gte


class FakeQdrantClientService:
    def __init__(self, points):
        self.points = list(points)
        self.upserts: list[SimpleNamespace] = []
        self.upserted_ids: list[str] = []
        self.collections_created: list[str] = []

    def collection_exists(self, collection_name):
        return collection_name in {"src", "src_v2"}

    @property
    def client(self):
        return SimpleNamespace(
            get_collections=lambda: SimpleNamespace(
                collections=[SimpleNamespace(name="src")]
            )
        )

    def count(self, collection_name, count_filter=None, exact=True):
        threshold = _threshold(count_filter)
        if threshold is None:
            return len(self.points)
        return sum(
            1
            for point in self.points
            if (point.payload.get("retention_value") or 0) >= threshold
        )

    def scroll(
        self,
        collection_name,
        scroll_filter=None,
        limit=10,
        offset=None,
        with_payload=True,
        with_vectors=True,
    ):
        threshold = _threshold(scroll_filter)
        selected = [
            point
            for point in self.points
            if threshold is None
            or (point.payload.get("retention_value") or 0) >= threshold
        ]
        start = offset or 0
        batch = selected[start : start + limit]
        next_offset = start + len(batch)
        return batch, (next_offset if next_offset < len(selected) else None)

    def upsert(self, collection_name, points, wait=False):
        self.upserts.extend(points)
        self.upserted_ids.extend(str(point.id) for point in points)


class FakeEmbeddingService:
    def __init__(self):
        self.embed_calls: list[list[str]] = []

    def get_embedding(self, text):
        return [0.0, 0.0, 1.0]

    @property
    def inference(self):
        return self

    def embed_sync(self, texts):
        self.embed_calls.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeCollectionService:
    created: list[tuple[str, int]] = []

    def __init__(self, client_service, collection_name, vector_size):
        self.client_service = client_service
        self.collection_name = collection_name
        FakeCollectionService.created.append((collection_name, vector_size))


class MigrationScriptTests(unittest.TestCase):
    def setUp(self):
        FakeCollectionService.created = []
        self._patches = [
            patch.object(config, "INFERENCE_EMBEDDING_DIM", 3),
            patch.object(config, "QDRANT_COLLECTION_NAME", "src"),
            patch.object(config, "EMBEDDING_VERSION", "v2"),
        ]
        for active_patch in self._patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

    def _run(self, args, points):
        client = FakeQdrantClientService(points)
        embeddings = FakeEmbeddingService()
        with (
            patch.object(migrate_embeddings, "QdrantClientService", lambda: client),
            patch.object(migrate_embeddings, "EmbeddingService", lambda: embeddings),
            patch.object(
                migrate_embeddings,
                "LlmCacheCollectionService",
                FakeCollectionService,
            ),
        ):
            code = migrate_embeddings.main(args)
        return code, client, embeddings

    def test_dry_run_reports_a_plan_and_writes_nothing(self):
        code, client, _ = self._run(
            ["--write", "--dry-run"], [_point("a"), _point("b")]
        )
        self.assertEqual(code, 0)
        self.assertEqual(client.upserts, [])
        self.assertEqual(FakeCollectionService.created, [])

    def test_default_target_is_source_v2(self):
        code, client, _ = self._run(["--write"], [_point("a")])
        self.assertEqual(code, 0)
        self.assertEqual(FakeCollectionService.created[0][0], "src_v2")
        self.assertEqual(len(client.upserts), 1)

    def test_min_retention_value_migrates_only_valuable_points(self):
        points = [
            _point("cheap", retention_value=1.4),
            _point("costly", retention_value=9.0),
        ]

        code, client, _ = self._run(["--write", "--min-retention-value", "5"], points)

        self.assertEqual(code, 0)
        self.assertEqual(client.upserted_ids, ["costly"])

    def test_points_without_retention_metadata_are_skipped_by_the_threshold(self):
        points = [
            _point("legacy", retention_value=None),
            _point("scored", retention_value=6.0),
        ]

        _, client, _ = self._run(["--write", "--min-retention-value", "1"], points)

        self.assertEqual(client.upserted_ids, ["scored"])

    def test_limit_caps_the_number_of_migrated_points(self):
        points = [_point(f"p{i}") for i in range(5)]

        code, client, _ = self._run(
            ["--write", "--limit", "2", "--batch-size", "1"], points
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(client.upserts), 2)

    def test_high_value_seed_keeps_payload_and_sparse_vectors(self):
        point = _point("costly", retention_value=9.0)

        _, client, _ = self._run(["--write", "--min-retention-value", "5"], [point])

        migrated = client.upserts[0]
        self.assertEqual(migrated.payload["retention_value"], 9.0)
        # Dense vectors are rebuilt by the engine; sparse ones are copied as-is
        # because the BM25 tokenizer did not change.
        self.assertEqual(migrated.vector["prompt_embedding"], [0.1, 0.2, 0.3])
        self.assertEqual(migrated.vector["user_prompt_embedding"], [0.1, 0.2, 0.3])
        self.assertEqual(migrated.vector["system_prompt_bm25"].indices, [1])

    def test_source_and_target_must_differ(self):
        code, client, _ = self._run(
            ["--source", "src", "--target", "src", "--write"], [_point("a")]
        )
        self.assertEqual(code, 2)
        self.assertEqual(client.upserts, [])

    def test_invalid_batch_size_is_rejected(self):
        code, client, _ = self._run(["--write", "--batch-size", "0"], [_point("a")])
        self.assertEqual(code, 2)
        self.assertEqual(client.upserts, [])


if __name__ == "__main__":
    unittest.main()
