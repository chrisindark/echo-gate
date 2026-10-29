"""The judge's own request must name its provider.

`ChatCompletionRequest` validates `model` against `ALLOWED_MODELS[service_name]`,
and `service_name` defaults to `"ollama"`. Building the judge request without it
therefore raised `ValidationError` for every configured judge model
(`LLM_JUDGE_SERVICE` defaults to `google-genai` with `gemini-3.5-flash-lite`), which
`_run_judge_request`'s caller swallowed into a logged error — so shadow equivalence
recorded `None` forever and the judge never ran.
"""

import unittest
from unittest.mock import MagicMock

from app.core.config import config
from app.modules.verifier.judge_service import JudgeService


class JudgeRequestProviderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._saved = (config.LLM_JUDGE_SERVICE, config.LLM_JUDGE_MODEL)
        self.service = JudgeService(
            llm_provider_service=MagicMock(), cross_encoder_service=MagicMock()
        )
        self.requests = []

    def tearDown(self) -> None:
        config.LLM_JUDGE_SERVICE, config.LLM_JUDGE_MODEL = self._saved

    async def _capture(self, request):
        self.requests.append(request)
        return "YES"

    async def test_equivalence_judge_names_the_configured_provider(self) -> None:
        config.LLM_JUDGE_SERVICE = "google-genai"
        config.LLM_JUDGE_MODEL = "gemini-3.5-flash-lite"
        self.service._run_judge_request = self._capture

        verdict = await self.service.evaluate_equivalence("q", "cached", "fresh")

        self.assertIs(verdict, True)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].service_name, "google-genai")
        self.assertEqual(self.requests[0].model, "gemini-3.5-flash-lite")

    async def test_scoring_judge_names_the_configured_provider(self) -> None:
        config.LLM_JUDGE_SERVICE = "groq"
        config.LLM_JUDGE_MODEL = "openai/gpt-oss-20b"

        async def _capture_json(request):
            self.requests.append(request)
            return '{"relevance": 5}'

        self.service._run_judge_request = _capture_json

        result = await self.service.evaluate_by_llm("q", "answer")

        self.assertEqual(result, {"relevance": 5})
        self.assertEqual(self.requests[0].service_name, "groq")


if __name__ == "__main__":
    unittest.main()
