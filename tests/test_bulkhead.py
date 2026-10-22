import unittest

from app.core.bulkhead import BulkheadLimitExceededException, BulkheadManager


class TestBulkhead(unittest.IsolatedAsyncioTestCase):
    async def test_bulkhead_allows_within_limits(self):
        manager = BulkheadManager(global_max_concurrency=2, provider_max_concurrency=2)
        async with manager.acquire("openai"):
            self.assertEqual(manager.active_global_count, 1)
            self.assertEqual(manager.active_provider_count("openai"), 1)
        self.assertEqual(manager.active_global_count, 0)
        self.assertEqual(manager.active_provider_count("openai"), 0)

    async def test_global_concurrency_limit_sheds_load(self):
        manager = BulkheadManager(global_max_concurrency=1, provider_max_concurrency=5)
        async with manager.acquire("openai"):
            # Second concurrent request exceeds global limit
            with self.assertRaises(BulkheadLimitExceededException) as ctx:
                async with manager.acquire("groq"):
                    pass
            self.assertEqual(ctx.exception.status_code, 429)
            self.assertIn("Global", ctx.exception.detail)

    async def test_provider_concurrency_limit_sheds_load_for_that_provider(self):
        manager = BulkheadManager(global_max_concurrency=10, provider_max_concurrency=1)
        async with manager.acquire("openai"):
            # Same provider exceeds provider limit
            with self.assertRaises(BulkheadLimitExceededException) as ctx:
                async with manager.acquire("openai"):
                    pass
            self.assertEqual(ctx.exception.status_code, 429)
            self.assertIn("openai", ctx.exception.detail)

            # Different provider can still acquire permit!
            async with manager.acquire("gemini"):
                self.assertEqual(manager.active_provider_count("gemini"), 1)


if __name__ == "__main__":
    unittest.main()
