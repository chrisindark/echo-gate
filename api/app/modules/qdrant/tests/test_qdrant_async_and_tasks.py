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


if __name__ == "__main__":
    unittest.main()
