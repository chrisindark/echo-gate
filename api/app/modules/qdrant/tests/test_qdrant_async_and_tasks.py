import unittest
from unittest.mock import MagicMock, patch

from fastapi import BackgroundTasks

from app.modules.qdrant.llm_cache_collection_service import LlmCacheCollectionService
from app.modules.qdrant.tasks import (
    delete_expired_vectors,
    dispatch_delete_expired_vectors,
    execute_delete_expired_vectors_job,
)


class FakeAsyncQdrantClientService:
    def __init__(self):
        self.closed = False
        self.aclosed = False

    def close(self):
        self.closed = True

    async def aclose(self):
        self.aclosed = True

    def collection_exists(self, name):
        return False

    def create_collection(self, **kwargs):
        return True

    def get_collection(self, name):
        mock_info = MagicMock()
        mock_info.payload_schema = {}
        return mock_info

    def create_payload_index(self, **kwargs):
        return True

    def scroll(self, **kwargs):
        mock_record = MagicMock()
        mock_record.payload = {"response": {"choices": []}, "intent": "general"}
        mock_record.id = "point-123"
        return [mock_record], None

    def query_points(self, **kwargs):
        mock_point = MagicMock()
        mock_point.payload = {"response": {"choices": []}}
        mock_point.score = 0.95
        mock_point.id = "point-123"
        mock_resp = MagicMock()
        mock_resp.points = [mock_point]
        return mock_resp

    def upsert(self, **kwargs):
        return MagicMock()

    def delete(self, **kwargs):
        return MagicMock()

    def retrieve(self, **kwargs):
        mock_record = MagicMock()
        mock_record.payload = {"retention_score": 1.0, "hit_count": 0}
        return [mock_record]

    def set_payload(self, **kwargs):
        return MagicMock()

    def count(self, **kwargs):
        return 0


class TestQdrantAsyncMethods(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake_client = FakeAsyncQdrantClientService()
        self.service = LlmCacheCollectionService(client_service=self.fake_client)

    async def test_search_exact_async(self):
        payload, point_id = await self.service.search_exact_async(
            "hash123", tenant_id="tenant-a"
        )
        self.assertEqual(point_id, "point-123")
        self.assertIsNotNone(payload)

    async def test_search_by_exact_hash_async(self):
        records = await self.service.search_by_exact_hash_async("hash123")
        self.assertIsInstance(records, list)

    async def test_query_points_rrf_async(self):
        matches = await self.service.query_points_rrf_async(
            user_prompt_vector=[0.1] * 384,
            limit=5,
        )
        self.assertIsInstance(matches, list)

    async def test_query_points_dense_async(self):
        matches = await self.service.query_points_dense_async(
            vector=[0.1] * 384,
            using="prompt_embedding",
            limit=5,
        )
        self.assertIsInstance(matches, list)

    async def test_upsert_async(self):
        point_id = await self.service.upsert_async(
            prompt="Hello world",
            response={"choices": []},
            exact_hash="hash123",
            prompt_vector=[0.1] * 384,
        )
        self.assertIsNotNone(point_id)

    async def test_delete_point_async(self):
        deleted = await self.service.delete_point_async("point-123")
        self.assertTrue(deleted)

    async def test_record_hit_async(self):
        recorded = await self.service.record_hit_async("point-123")
        self.assertTrue(recorded)

    async def test_ensure_indexes_async(self):
        await self.service.ensure_indexes_async()

    async def test_bulk_evict_async(self):
        result = await self.service.bulk_evict_async(dry_run=True, intent="query")
        self.assertIn("matched", result)

    async def test_delete_expired_async(self):
        await self.service.delete_expired_async()

    async def test_aclose(self):
        await self.service.aclose()
        self.assertTrue(self.fake_client.aclosed)


class TestQdrantTasks(unittest.TestCase):
    @patch(
        "app.modules.qdrant.tasks.DependencyContainer.get_llm_cache_collection_service"
    )
    def test_execute_delete_expired_vectors_job_success(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        execute_delete_expired_vectors_job()
        mock_service.delete_expired.assert_called_once()

    @patch(
        "app.modules.qdrant.tasks.DependencyContainer.get_llm_cache_collection_service"
    )
    def test_execute_delete_expired_vectors_job_exception_handled(
        self, mock_get_service
    ):
        mock_service = MagicMock()
        mock_service.delete_expired.side_effect = RuntimeError("Qdrant unavailable")
        mock_get_service.return_value = mock_service
        # Must not raise
        execute_delete_expired_vectors_job()

    @patch("app.modules.qdrant.tasks.execute_delete_expired_vectors_job")
    def test_delete_expired_vectors_celery_task(self, mock_job):
        delete_expired_vectors()
        mock_job.assert_called_once()

    @patch("app.modules.qdrant.tasks.dispatch_task")
    def test_dispatch_delete_expired_vectors(self, mock_dispatch):
        mock_dispatch.return_value = ("qdrant-bg-123", True)
        bg = BackgroundTasks()
        task_id, dispatched = dispatch_delete_expired_vectors(background_tasks=bg)
        self.assertEqual(task_id, "qdrant-bg-123")
        self.assertTrue(dispatched)
        mock_dispatch.assert_called_once()


class TestLlmCacheCollectionServiceIndexesAndLifecycle(
    unittest.IsolatedAsyncioTestCase
):
    def test_ensure_indexes_creation_and_exceptions(self):
        client = FakeAsyncQdrantClientService()
        mock_info = MagicMock()
        mock_info.payload_schema = {"created_at": MagicMock()}  # 1 existing
        client.get_collection = lambda name: mock_info

        created_indexes = []

        def mock_create_idx(**kwargs):
            if kwargs.get("field_name") == "prompt_hash":
                raise RuntimeError("index error")
            created_indexes.append(kwargs.get("field_name"))

        client.create_payload_index = mock_create_idx

        service = LlmCacheCollectionService(client_service=client)
        client.collection_exists = lambda name: True
        service.ensure_indexes()
        self.assertIn("tenant_id", created_indexes)
        self.assertNotIn("created_at", created_indexes)

    def test_init_collection_dimension_mismatch(self):
        client = FakeAsyncQdrantClientService()
        client.collection_exists = lambda name: True
        mock_info = MagicMock()
        mock_vec = MagicMock()
        mock_vec.size = 128
        mock_info.config.params.vectors = {"prompt_embedding": mock_vec}
        client.get_collection = lambda name: mock_info

        with self.assertRaises(ValueError) as ctx:
            LlmCacheCollectionService(client_service=client, vector_size=384)
        self.assertIn("Vector dimension mismatch", str(ctx.exception))

    async def test_close_and_aclose(self):
        client = FakeAsyncQdrantClientService()
        service = LlmCacheCollectionService(client_service=client)
        service.close()
        self.assertTrue(client.closed)

        await service.aclose()
        self.assertTrue(client.aclosed)

    def test_delete_point_and_upsert_error_handling(self):
        client = FakeAsyncQdrantClientService()
        service = LlmCacheCollectionService(client_service=client)

        # delete_point success and error
        self.assertTrue(service.delete_point("pt-1"))
        client.delete = MagicMock(side_effect=RuntimeError("Qdrant error"))
        self.assertFalse(service.delete_point("pt-1"))

        # upsert error returns None
        client.upsert = MagicMock(side_effect=RuntimeError("upsert error"))
        pid = service.upsert(
            prompt="hi",
            response={"choices": []},
            system_prompt_vector=[0.1] * 384,
            system_prompt_sparse=None,
            user_prompt_vector=[0.1] * 384,
            user_prompt_sparse=None,
            exact_hash="h",
            model="m",
            service_name="s",
        )
        self.assertIsNone(pid)

    def test_delete_expired_exception_caught(self):
        client = FakeAsyncQdrantClientService()
        service = LlmCacheCollectionService(client_service=client)
        service.evict_expired = MagicMock(side_effect=RuntimeError("scroll error"))
        # Should not raise
        service.delete_expired()

    def test_create_collection_with_replication_factors(self):
        from app.core.config import config

        client = FakeAsyncQdrantClientService()
        created_kwargs = {}

        def mock_create(**kwargs):
            created_kwargs.update(kwargs)
            return True

        client.create_collection = mock_create

        with (
            patch.object(config, "QDRANT_REPLICATION_FACTOR", 2),
            patch.object(config, "QDRANT_WRITE_CONSISTENCY_FACTOR", 1),
        ):
            _ = LlmCacheCollectionService(client_service=client)
            self.assertEqual(created_kwargs.get("replication_factor"), 2)
            self.assertEqual(created_kwargs.get("write_consistency_factor"), 1)

    def test_evict_expired_retention_disabled_and_enabled(self):
        from app.core.config import config

        client = FakeAsyncQdrantClientService()
        service = LlmCacheCollectionService(client_service=client)
        service._delete_points_matching = MagicMock()

        # Retention disabled
        with patch.object(config, "CACHE_RETENTION_ENABLED", False):
            service.evict_expired()
            self.assertEqual(service._delete_points_matching.call_count, 1)

        # Retention enabled
        service._delete_points_matching.reset_mock()
        with (
            patch.object(config, "CACHE_RETENTION_ENABLED", True),
            patch.object(config, "CACHE_RETENTION_PROTECT_THRESHOLD", 0.5),
            patch.object(config, "CACHE_RETENTION_GRACE_PERIOD_SECONDS", 3600),
        ):
            service.evict_expired()
            self.assertEqual(service._delete_points_matching.call_count, 2)

    def test_evict_over_capacity(self):
        from app.core.config import config

        client = FakeAsyncQdrantClientService()
        service = LlmCacheCollectionService(client_service=client)

        # Max points <= 0
        with patch.object(config, "CACHE_RETENTION_MAX_POINTS", 0):
            self.assertEqual(service.evict_over_capacity(), 0)

        # Under capacity
        with patch.object(config, "CACHE_RETENTION_MAX_POINTS", 100):
            client.count = MagicMock(return_value=80)
            self.assertEqual(service.evict_over_capacity(), 0)

            # Over capacity with points to scroll
            client.count = MagicMock(return_value=105)
            r1, r2 = MagicMock(), MagicMock()
            r1.id, r2.id = "p-1", "p-2"
            client.scroll = MagicMock(return_value=([r1, r2], None))
            client.delete = MagicMock()
            deleted = service.evict_over_capacity()
            self.assertEqual(deleted, 2)
            client.delete.assert_called_once()

            # Over capacity but scroll returns nothing
            client.scroll = MagicMock(return_value=([], None))
            self.assertEqual(service.evict_over_capacity(), 0)

    def test_bulk_evict_lifecycle(self):
        client = FakeAsyncQdrantClientService()
        service = LlmCacheCollectionService(client_service=client)

        # Empty filter raises ValueError
        with self.assertRaises(ValueError):
            service.bulk_evict()

        # Non-positive older_than_days raises ValueError
        with self.assertRaises(ValueError):
            service.bulk_evict(intent="general", older_than_days=-1)

        # matched == 0
        client.count = MagicMock(return_value=0)
        res = service.bulk_evict(intent="general", dry_run=False)
        self.assertEqual(res["matched"], 0)
        self.assertEqual(res["deleted"], 0)

        # dry_run == True
        client.count = MagicMock(return_value=10)
        res_dry = service.bulk_evict(intent="general", dry_run=True)
        self.assertEqual(res_dry["matched"], 10)
        self.assertEqual(res_dry["deleted"], 0)

        # matched > limit
        client.count = MagicMock(return_value=100)
        with self.assertRaises(ValueError):
            service.bulk_evict(intent="general", limit=50, dry_run=False)

        # successful bulk eviction
        client.count = MagicMock(return_value=2)
        rec1 = MagicMock(id="p1", payload={"exact_hash": "hash1"})
        rec2 = MagicMock(id="p2", payload={"exact_hash": "hash2"})
        client.scroll = MagicMock(return_value=([rec1, rec2], None))
        client.delete = MagicMock()

        res_ok = service.bulk_evict(intent="general", limit=10, dry_run=False)
        self.assertEqual(res_ok["matched"], 2)
        self.assertEqual(res_ok["deleted"], 2)
        self.assertEqual(set(res_ok["exact_hashes"]), {"hash1", "hash2"})


if __name__ == "__main__":
    unittest.main()
