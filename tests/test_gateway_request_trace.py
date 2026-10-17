import time
import unittest
from unittest.mock import MagicMock

from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
)
from app.modules.gateway_requests.gateway_requests_schema import RoutingDecision
from app.modules.llm.llm_router_service import LlmRouterService


def _response(cache_info: dict) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-trace",
        created=1700000000,
        model="gemini-3.1-flash-lite",
        choices=[
            Choice(
                index=0,
                message=ChatMessage(role="assistant", content="answer"),
                finish_reason="stop",
            )
        ],
        cache_info=cache_info,
    )


class TestGatewayRequestTraceScore(unittest.TestCase):
    def setUp(self):
        self.gateway_requests_service = MagicMock()
        self.router = LlmRouterService(
            llm_provider_service=MagicMock(),
            embedding_service=MagicMock(),
            llm_cache_service=MagicMock(),
            redis_service=MagicMock(),
            reranker_service=MagicMock(),
            intent_classifier_service=MagicMock(),
            entity_extractor_service=MagicMock(),
            llm_usage_service=MagicMock(),
            gateway_requests_service=self.gateway_requests_service,
        )

    def _log(self, cache_info: dict) -> dict:
        request = ChatCompletionRequest(
            service_name="claude",
            model="claude-3-5-sonnet-20241022",
            messages=[
                ChatMessage(role="user", content="What is the capital of France?")
            ],
        )
        response = _response(cache_info)
        # `_log_gateway_request` is async but only offloads the DB write through
        # `asyncio.to_thread`; call it synchronously via asyncio.run to keep the
        # test fixture small.
        import asyncio

        asyncio.run(
            self.router._log_gateway_request(
                request=request,
                response=response,
                service_name="google-genai",
                start_time=time.perf_counter(),
                exact_hash="deadbeef",
                routing_decision=RoutingDecision.LLM,
            )
        )
        self.gateway_requests_service.log_request.assert_called_once()
        return self.gateway_requests_service.log_request.call_args[0][0]

    def test_semantic_miss_falls_back_to_top_1_rerank_score(self):
        # A rejected semantic candidate never sets "rerank_score"; only the best
        # candidate's "top_1_rerank_score" is known. The trace must still record
        # the composite score that drove the decision.
        log_data = self._log(
            {
                "matched": False,
                "accepted": False,
                "source": "llm",
                "top_1_rerank_score": 0.8123,
                "score_breakdown": {"base_score": 0.85, "final_score": 0.8123},
            }
        )

        self.assertEqual(float(log_data.rerank_score), 0.8123)
        self.assertEqual(log_data.score_breakdown["final_score"], 0.8123)

    def test_accepted_hit_prefers_rerank_score(self):
        log_data = self._log(
            {
                "matched": True,
                "accepted": True,
                "source": "qdrant_semantic_path",
                "rerank_score": 0.9731,
                "top_1_rerank_score": 0.9731,
            }
        )

        self.assertEqual(float(log_data.rerank_score), 0.9731)

    def test_no_semantic_candidate_leaves_score_null(self):
        log_data = self._log({"matched": False, "accepted": False, "source": "llm"})

        self.assertIsNone(log_data.rerank_score)


if __name__ == "__main__":
    unittest.main()
