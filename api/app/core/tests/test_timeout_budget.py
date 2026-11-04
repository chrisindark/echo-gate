import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
from starlette.testclient import TestClient

from app.core.dependencies import get_llm_router_service
from app.core.guards import llm_quota_guard
from app.main import app
from app.modules.chat.chat_schema import ChatCompletionResponse, ChatMessage, Choice
from app.modules.llm.llm_router_service import LlmRouterService


class TestTimeoutBudget(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        app.dependency_overrides[llm_quota_guard] = lambda: None

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_timeout_budget_exhausted_aborts_early(self):
        mock_response = ChatCompletionResponse(
            id="chatcmpl-test",
            created=1234567,
            model="gpt-4o-mini",
            choices=[
                Choice(index=0, message=ChatMessage(role="assistant", content="Hello"))
            ],
        )

        mock_router = MagicMock(spec=LlmRouterService)

        async def delayed_completion(req, api_key=None, timeout_budget_ms=None):
            if timeout_budget_ms and timeout_budget_ms < 100:
                raise HTTPException(
                    status_code=504,
                    detail="Request timeout budget exceeded before upstream dispatch.",
                )
            return mock_response

        mock_router.generate_completion = AsyncMock(side_effect=delayed_completion)
        app.dependency_overrides[get_llm_router_service] = lambda: mock_router

        response = self.client.post(
            "/api/v1/chat/completions",
            json={
                "service_name": "openai",
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"X-Timeout-Budget-Ms": "50"},
        )
        self.assertEqual(response.status_code, 504)
        self.assertIn("timeout budget", response.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
