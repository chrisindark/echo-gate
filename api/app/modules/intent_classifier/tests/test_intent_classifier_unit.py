import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    Usage,
)
from app.modules.intent_classifier.intent_classifier_service import (
    IntentClassifierService,
)
from app.modules.intent_classifier.intent_schema import IntentEnum


def _make_response(content: str) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="cmpl-test",
        created=12345,
        model="gpt-4o-mini",
        choices=[
            Choice(
                index=0,
                message=ChatMessage(role="assistant", content=content),
                finish_reason="stop",
            )
        ],
        usage=Usage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
    )


class TestIntentClassifierUnit(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_provider = MagicMock()
        self.service = IntentClassifierService(
            llm_provider_service=self.mock_provider,
            model="gpt-4o-mini",
            service="openai",
            cache_size=2,
            timeout_seconds=2.0,
            max_tokens=64,
            max_retries=0,
        )

    def test_clean_and_parse_json(self):
        # Empty
        self.assertEqual(IntentClassifierService._clean_and_parse_json(""), {})
        self.assertEqual(IntentClassifierService._clean_and_parse_json(None), {})

        # Plain JSON
        self.assertEqual(
            IntentClassifierService._clean_and_parse_json('{"a": 1}'), {"a": 1}
        )

        # Markdown block ```json ... ```
        self.assertEqual(
            IntentClassifierService._clean_and_parse_json('```json\n{"b": 2}\n```'),
            {"b": 2},
        )

        # Markdown block ``` ... ```
        self.assertEqual(
            IntentClassifierService._clean_and_parse_json('```\n{"c": 3}\n```'),
            {"c": 3},
        )

        # JSON embedded in prose
        text = 'Here is your JSON classification:\n{"intent": "general_query"}\nHope this helps!'
        self.assertEqual(
            IntentClassifierService._clean_and_parse_json(text),
            {"intent": "general_query"},
        )

        # Truly unparseable
        with self.assertRaises(json.JSONDecodeError):
            IntentClassifierService._clean_and_parse_json("not a json at all")

    async def test_execute_llm_request_fallbacks(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hi")],
            response_format={"type": "json_schema"},
        )

        resp_ok = _make_response('{"intent": "general_query"}')

        # 1. Normal success
        self.mock_provider.generate_completion = AsyncMock(return_value=resp_ok)
        res1 = await self.service._execute_llm_request(req)
        self.assertEqual(res1.choices[0].message.content, '{"intent": "general_query"}')

        # 2. json_schema rejected (400) -> retried with json_object
        self.mock_provider.generate_completion = AsyncMock(
            side_effect=[
                HTTPException(status_code=400, detail="json_schema not supported"),
                resp_ok,
            ]
        )
        await self.service._execute_llm_request(req)
        self.assertEqual(self.mock_provider.generate_completion.call_count, 2)

        # 3. json_schema rejected (400), json_object also rejected (400) -> retried with None
        self.mock_provider.generate_completion = AsyncMock(
            side_effect=[
                HTTPException(status_code=400, detail="json_schema bad"),
                HTTPException(status_code=400, detail="json_object bad"),
                resp_ok,
            ]
        )
        await self.service._execute_llm_request(req)
        self.assertEqual(self.mock_provider.generate_completion.call_count, 3)

        # 4. Initial json_object rejected (400) -> retried with None
        req_obj = req.model_copy(update={"response_format": {"type": "json_object"}})
        self.mock_provider.generate_completion = AsyncMock(
            side_effect=[
                HTTPException(status_code=400, detail="json_object bad"),
                resp_ok,
            ]
        )
        await self.service._execute_llm_request(req_obj)
        self.assertEqual(self.mock_provider.generate_completion.call_count, 2)

        # 5. Non-400 error re-raised
        self.mock_provider.generate_completion = AsyncMock(
            side_effect=HTTPException(status_code=500, detail="Server error")
        )
        with self.assertRaises(HTTPException) as ctx:
            await self.service._execute_llm_request(req)
        self.assertEqual(ctx.exception.status_code, 500)

    async def test_classify_intent_parsing_and_normalization(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[
                ChatMessage(role="user", content="Test classification normalization")
            ],
        )

        payload_data = {
            "intent": "EMPTY",  # Should map to GENERAL_QUERY
            "time_sensitivity": 1.5,  # Clamped to 1.0
            "core_operation": "null",  # Converted to ""
            "core_subject": "N/A",  # Converted to ""
            "subject_modifier": "None",  # Converted to None
            "action_modifier": "fast search",  # Kept
        }
        self.mock_provider.generate_completion = AsyncMock(
            return_value=_make_response(json.dumps(payload_data))
        )

        res = await self.service.classify_intent(req)
        self.assertIsNotNone(res)
        self.assertEqual(res.intent, IntentEnum.GENERAL_QUERY)
        self.assertEqual(res.time_sensitivity, 1.0)
        self.assertEqual(res.core_operation, "")
        self.assertEqual(res.core_subject, "")
        self.assertIsNone(res.subject_modifier)
        self.assertEqual(res.action_modifier, "fast search")

        # Cache hit returns same object without calling provider
        self.mock_provider.generate_completion.reset_mock()
        cached_res = await self.service.classify_intent(req)
        self.assertEqual(cached_res, res)
        self.mock_provider.generate_completion.assert_not_called()

        # Cache eviction when exceeding max_cache_size (configured as 2)
        req2 = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Second prompt")],
        )
        req3 = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Third prompt")],
        )
        self.mock_provider.generate_completion = AsyncMock(
            return_value=_make_response(json.dumps({"intent": "coding"}))
        )
        await self.service.classify_intent(req2)
        await self.service.classify_intent(req3)
        self.assertLessEqual(len(self.service._cache), 2)

        # Clear cache
        self.service.clear_cache()
        self.assertEqual(len(self.service._cache), 0)

    async def test_classify_intent_invalid_payload_returns_none(self):
        req = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="garbage output")],
        )
        self.mock_provider.generate_completion = AsyncMock(
            return_value=_make_response("Totally not json at all!!!")
        )
        res = await self.service.classify_intent(req)
        self.assertIsNone(res)

        # Empty content in choice gracefully defaults to GENERAL_QUERY
        self.mock_provider.generate_completion = AsyncMock(
            return_value=_make_response("")
        )
        res_empty = await self.service.classify_intent(req)
        self.assertIsNotNone(res_empty)
        self.assertEqual(res_empty.intent, IntentEnum.GENERAL_QUERY)
