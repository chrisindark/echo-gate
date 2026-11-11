import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from qdrant_client.http import models

from app.modules.qdrant.qdrant_client_service import QdrantClientService


class TestQdrantClientService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_async_client = AsyncMock()
        self.service = QdrantClientService(
            client=self.mock_client,
            async_client=self.mock_async_client,
        )

    def test_get_collections(self):
        c1 = MagicMock()
        c1.name = "collection-a"
        c2 = MagicMock()
        c2.name = "collection-b"
        mock_resp = MagicMock()
        mock_resp.collections = [c1, c2]
        self.mock_client.get_collections.return_value = mock_resp

        names = self.service.get_collections()
        self.assertEqual(names, ["collection-a", "collection-b"])

    def test_collection_exists(self):
        self.mock_client.collection_exists.return_value = True
        self.assertTrue(self.service.collection_exists("col1"))

    def test_get_collection(self):
        mock_info = MagicMock()
        self.mock_client.get_collection.return_value = mock_info
        self.assertEqual(self.service.get_collection("col1"), mock_info)

    def test_create_collection(self):
        self.mock_client.create_collection.return_value = True
        created = self.service.create_collection(
            collection_name="col1",
            vectors_config={},
        )
        self.assertTrue(created)
        self.mock_client.create_collection.assert_called_once()

    def test_delete_collection(self):
        self.mock_client.delete_collection.return_value = True
        deleted = self.service.delete_collection("col1")
        self.assertTrue(deleted)
        self.mock_client.delete_collection.assert_called_once()

    def test_create_payload_index(self):
        self.mock_client.create_payload_index.return_value = True
        idx = self.service.create_payload_index(
            collection_name="col1",
            field_name="field1",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        self.assertTrue(idx)

    def test_scroll(self):
        self.mock_client.scroll.return_value = ([], None)
        records, offset = self.service.scroll("col1")
        self.assertEqual(records, [])
        self.assertIsNone(offset)

    def test_query_points(self):
        mock_resp = MagicMock()
        self.mock_client.query_points.return_value = mock_resp
        resp = self.service.query_points("col1", query=[0.1] * 128)
        self.assertEqual(resp, mock_resp)

    def test_retrieve(self):
        self.mock_client.retrieve.return_value = []
        records = self.service.retrieve("col1", ids=["1", "2"])
        self.assertEqual(records, [])

    def test_set_payload(self):
        self.mock_client.set_payload.return_value = True
        res = self.service.set_payload("col1", payload={"key": "val"}, points=["1"])
        self.assertTrue(res)

    def test_count(self):
        mock_res = MagicMock()
        mock_res.count = 42
        self.mock_client.count.return_value = mock_res
        count = self.service.count("col1")
        self.assertEqual(count, 42)

    def test_upsert(self):
        self.mock_client.upsert.return_value = True
        res = self.service.upsert("col1", points=[])
        self.assertTrue(res)

    def test_delete(self):
        self.mock_client.delete.return_value = True
        res = self.service.delete("col1", points_selector=[])
        self.assertTrue(res)

    def test_close(self):
        self.service.close()
        self.mock_client.close.assert_called_once()

    async def test_aclose(self):
        await self.service.aclose()
        self.mock_client.close.assert_called_once()
        self.mock_async_client.close.assert_called_once()

    def test_lazy_async_client_property(self):
        service = QdrantClientService(client=self.mock_client)
        with patch(
            "app.modules.qdrant.qdrant_client_service.AsyncQdrantClient"
        ) as mock_async_cls:
            mock_inst = AsyncMock()
            mock_async_cls.return_value = mock_inst
            async_client = service.async_client
            self.assertEqual(async_client, mock_inst)
            # Second access returns cached instance
            self.assertEqual(service.async_client, mock_inst)
            mock_async_cls.assert_called_once()


if __name__ == "__main__":
    unittest.main()
