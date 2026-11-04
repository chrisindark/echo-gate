"""Intent classifier timeout / failure degradation tests.

Invariant under test: classification is advisory. A timeout or provider error
must degrade the *request* to a cache miss (``None``) without raising, and the
failure must not be cached, or one transient timeout would suppress semantic
cache lookups for that prompt for the life of the process.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    Usage,
)
from app.modules.intent_classifier import intent_classifier_service as classifier_module
from app.modules.intent_classifier.intent_classifier_service import (
    IntentClassifierService,
)
from app.modules.intent_classifier.intent_schema import IntentEnum

TIMEOUT = 1.5


def _request() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        service_name="intent",
        model="gpt-4o-mini",
        messages=[ChatMessage(role="user", content="what is the weather in Berlin?")],
    )


def _classifier() -> IntentClassifierService:
    return IntentClassifierService(
        llm_provider_service=MagicMock(),
        model="gpt-4o-mini",
        service="intent",
        timeout_seconds=TIMEOUT,
        max_tokens=64,
        max_retries=0,
    )


def _classification_response(payload: str) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-intent",
        created=1,
        model="gpt-4o-mini",
        choices=[
            Choice(
                index=0,
                message=ChatMessage(role="assistant", content=payload),
                finish_reason="stop",
            )
        ],
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


async def test_timeout_degrades_to_none(monkeypatch):
    """A TimeoutError from the retry wrapper is swallowed into a cache miss."""
    classifier = _classifier()

    async def boom(*args, **kwargs):
        raise TimeoutError("intent classification exceeded its budget")

    monkeypatch.setattr(classifier_module, "execute_with_retry", boom)

    assert await classifier.classify_intent(_request()) is None


async def test_timeout_uses_configured_budget(monkeypatch):
    """The configured timeout is the one handed to the retry wrapper."""
    classifier = _classifier()
    seen: dict[str, object] = {}

    async def capture(coro_fn, *args, **kwargs):
        seen.update(kwargs)
        raise TimeoutError

    monkeypatch.setattr(classifier_module, "execute_with_retry", capture)

    await classifier.classify_intent(_request())

    assert seen["timeout_seconds"] == TIMEOUT
    assert seen["max_retries"] == 0


async def test_timeout_is_not_cached(monkeypatch):
    """A second call must hit the provider again instead of replaying the failure."""
    classifier = _classifier()
    calls = 0

    async def flaky(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError
        return _classification_response(
            '{"intent": "weather", "time_sensitivity": 0.95, '
            '"core_operation": "check", "core_subject": "weather", '
            '"subject_modifier": null, "action_modifier": null}'
        )

    monkeypatch.setattr(classifier_module, "execute_with_retry", flaky)

    first = await classifier.classify_intent(_request())
    second = await classifier.classify_intent(_request())

    assert first is None
    assert calls == 2
    assert second is not None
    assert second.intent == IntentEnum.WEATHER


async def test_provider_error_degrades_to_none(monkeypatch):
    """Non-timeout provider failures degrade too, and are equally not cached."""
    classifier = _classifier()
    calls = 0

    async def upstream_down(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise HTTPException(status_code=503, detail="upstream unavailable")

    monkeypatch.setattr(classifier_module, "execute_with_retry", upstream_down)

    assert await classifier.classify_intent(_request()) is None
    assert await classifier.classify_intent(_request()) is None
    assert calls == 2


async def test_successful_classification_is_cached(monkeypatch):
    """The positive path caches, which is what makes the negative assertions meaningful."""
    classifier = _classifier()
    calls = 0

    async def ok(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _classification_response(
            '{"intent": "finance_market", "time_sensitivity": 0.99, '
            '"core_operation": "get price", "core_subject": "apple stock", '
            '"subject_modifier": "latest", "action_modifier": null}'
        )

    monkeypatch.setattr(classifier_module, "execute_with_retry", ok)

    first = await classifier.classify_intent(_request())
    second = await classifier.classify_intent(_request())

    assert calls == 1
    assert first is not None and second is not None
    assert first.intent == IntentEnum.FINANCE_MARKET
    assert first.subject_modifier == "latest"
    assert first.action_modifier is None
    assert second == first


@pytest.mark.parametrize(
    "intent,time_sensitivity,expected_cacheable",
    [
        (IntentEnum.WEATHER, 0.95, False),
        (IntentEnum.FINANCE_MARKET, 0.1, False),
        (IntentEnum.GENERAL_QUERY, 0.1, True),
    ],
)
def test_ttl_refuses_live_data(intent, time_sensitivity, expected_cacheable):
    """Degradation must not have loosened which intents are allowed to be cached."""
    classifier = _classifier()
    ttl = classifier.calculate_ttl(intent, time_sensitivity)
    assert (ttl is not None) is expected_cacheable


async def test_coroutine_fn_is_the_provider_call():
    """Sanity: the classifier calls generate_completion, not the router."""
    provider = MagicMock()
    provider.generate_completion = AsyncMock(
        return_value=_classification_response(
            '{"intent": "general_query", "time_sensitivity": 0.0, '
            '"core_operation": "explain", "core_subject": "python", '
            '"subject_modifier": null, "action_modifier": null}'
        )
    )
    classifier = IntentClassifierService(
        llm_provider_service=provider,
        model="gpt-4o-mini",
        service="intent",
        timeout_seconds=TIMEOUT,
        max_tokens=64,
        max_retries=0,
    )

    result = await classifier.classify_intent(_request())

    assert result is not None
    assert provider.generate_completion.await_count == 1
    (sent_request,) = provider.generate_completion.call_args.args
    assert sent_request.response_format["type"] == "json_schema"
    assert provider.generate_completion.call_args.kwargs["service_name"] == "intent"
