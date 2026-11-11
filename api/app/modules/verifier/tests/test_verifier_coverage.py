from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import config
from app.main import app
from app.modules.chat.chat_schema import ChatCompletionResponse, ChatMessage, Choice
from app.modules.verifier.cross_encoder_service import (
    CrossEncoderService,
    InferenceUnavailable,
)
from app.modules.verifier.instruction_service import InstructionVerifier
from app.modules.verifier.judge_service import JudgeService
from app.modules.verifier.verifier_controller import get_verifier_service


def test_instruction_verifier_parse_number():
    verifier = InstructionVerifier()
    assert verifier._parse_number("5") == 5
    assert verifier._parse_number("one") == 1
    assert verifier._parse_number("ten") == 10
    assert verifier._parse_number("eleven") == 0


def test_instruction_verifier_empty_candidates():
    verifier = InstructionVerifier()
    assert verifier.verify_instruction("Respond in json", []) == []


def test_instruction_verifier_no_constraints():
    verifier = InstructionVerifier()
    query = "Tell me about space exploration"
    candidates = ["Space is vast.", "NASA explores Mars."]
    assert verifier.verify_instruction(query, candidates) == [1.0, 1.0]


def test_instruction_verifier_json_constraint():
    verifier = InstructionVerifier()
    query = "Please respond in json format"
    candidates = [
        '{"status": "ok"}',
        '```json\n{"status": "ok"}\n```',
        "Not a json string",
    ]
    scores = verifier.verify_instruction(query, candidates)
    assert scores == [1.0, 1.0, 0.0]


def test_instruction_verifier_table_constraint():
    verifier = InstructionVerifier()
    query = "Provide data in a table format"
    candidates = [
        "| Header 1 | Header 2 |\n|---|---|\n| Val 1 | Val 2 |",
        "Just plain text without table",
    ]
    scores = verifier.verify_instruction(query, candidates)
    assert scores == [1.0, 0.0]


def test_instruction_verifier_bullets_and_count():
    verifier = InstructionVerifier()
    query = "List exactly 3 bullet points"
    c_pass = "- Point 1\n- Point 2\n- Point 3"
    c_fail = "Just a single paragraph without any bullet points."
    scores = verifier.verify_instruction(query, [c_pass, c_fail])
    assert scores[0] == 1.0
    assert scores[1] == 0.0


def test_instruction_verifier_word_and_sentence_limits():
    verifier = InstructionVerifier()
    query = "Give me answer under 2 sentences and less than 10 words"
    c1 = "Hello world. Goodbye world."
    c2 = "This is sentence one. This is sentence two. This is sentence three which exceeds the limit."
    scores = verifier.verify_instruction(query, [c1, c2])
    assert scores[0] == 1.0
    assert scores[1] < 1.0

    # json_only flag
    json_query = "Respond in json format and under 5 words"
    scores_json_only = verifier.verify_instruction(
        json_query, ['{"ok": true}'], json_only=True
    )
    assert scores_json_only == [1.0]


@pytest.fixture
def mock_llm_provider():
    return MagicMock()


@pytest.fixture
def mock_cross_encoder():
    mock = MagicMock(spec=CrossEncoderService)
    mock.relevance_predict_async = AsyncMock(return_value=[8.0])
    mock.nli_predict_async = AsyncMock(return_value=[[-2.0, 5.0, 0.0]])
    return mock


@pytest.mark.asyncio
async def test_judge_service_run_judge_request_providers(
    mock_llm_provider, mock_cross_encoder
):
    judge = JudgeService(mock_llm_provider, mock_cross_encoder)

    resp = ChatCompletionResponse(
        id="1",
        object="chat.completion",
        created=123,
        model="test",
        choices=[
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant", content='{"llm_relevance_score": 5}'
                ),
                finish_reason="stop",
            )
        ],
    )

    req = MagicMock()

    # Gemini
    with patch.object(config, "LLM_JUDGE_SERVICE", "gemini"):
        mock_llm_provider.generate_gemini_completion = AsyncMock(return_value=resp)
        result = await judge._run_judge_request(req)
        assert result == '{"llm_relevance_score": 5}'

    # OpenAI
    with patch.object(config, "LLM_JUDGE_SERVICE", "openai"):
        mock_llm_provider.generate_openai_completion = AsyncMock(return_value=resp)
        result = await judge._run_judge_request(req)
        assert result == '{"llm_relevance_score": 5}'

    # Groq
    with patch.object(config, "LLM_JUDGE_SERVICE", "groq"):
        mock_llm_provider.generate_groq_completion = AsyncMock(return_value=resp)
        result = await judge._run_judge_request(req)
        assert result == '{"llm_relevance_score": 5}'

    # Claude
    with patch.object(config, "LLM_JUDGE_SERVICE", "claude"):
        mock_llm_provider.generate_claude_completion = AsyncMock(return_value=resp)
        result = await judge._run_judge_request(req)
        assert result == '{"llm_relevance_score": 5}'

    # Unsupported
    with patch.object(config, "LLM_JUDGE_SERVICE", "unknown_provider"):
        result = await judge._run_judge_request(req)
        assert result is None

    # Empty choices / content
    empty_resp = ChatCompletionResponse(
        id="1",
        object="chat.completion",
        created=123,
        model="test",
        choices=[],
    )
    with patch.object(config, "LLM_JUDGE_SERVICE", "gemini"):
        mock_llm_provider.generate_gemini_completion = AsyncMock(
            return_value=empty_resp
        )
        result = await judge._run_judge_request(req)
        assert result is None

    # Exception
    with patch.object(config, "LLM_JUDGE_SERVICE", "gemini"):
        mock_llm_provider.generate_gemini_completion = AsyncMock(
            side_effect=RuntimeError("API down")
        )
        result = await judge._run_judge_request(req)
        assert result is None


@pytest.mark.asyncio
async def test_judge_service_evaluate_by_llm(mock_llm_provider, mock_cross_encoder):
    judge = JudgeService(mock_llm_provider, mock_cross_encoder)

    with patch.object(
        judge, "_run_judge_request", AsyncMock(return_value='{"score": 5}')
    ):
        res = await judge.evaluate_by_llm("query", "response")
        assert res == {"score": 5}

    with patch.object(judge, "_run_judge_request", AsyncMock(return_value=None)):
        res = await judge.evaluate_by_llm("query", "response")
        assert res == {}

    with patch.object(judge, "_run_judge_request", AsyncMock(return_value="not json")):
        res = await judge.evaluate_by_llm("query", "response")
        assert res == {}


@pytest.mark.asyncio
async def test_judge_service_evaluate_equivalence(
    mock_llm_provider, mock_cross_encoder
):
    judge = JudgeService(mock_llm_provider, mock_cross_encoder)

    with patch.object(judge, "_run_judge_request", AsyncMock(return_value=None)):
        assert await judge.evaluate_equivalence("q", "c", "a") is None

    with patch.object(judge, "_run_judge_request", AsyncMock(return_value="YES")):
        assert await judge.evaluate_equivalence("q", "c", "a") is True

    with patch.object(judge, "_run_judge_request", AsyncMock(return_value="NO")):
        assert await judge.evaluate_equivalence("q", "c", "a") is False

    with patch.object(
        judge, "_run_judge_request", AsyncMock(return_value="Verdict: YES, equivalent")
    ):
        assert await judge.evaluate_equivalence("q", "c", "a") is True

    with patch.object(
        judge, "_run_judge_request", AsyncMock(return_value="Verdict: NO, different")
    ):
        assert await judge.evaluate_equivalence("q", "c", "a") is False

    with patch.object(judge, "_run_judge_request", AsyncMock(return_value="MAYBE")):
        assert await judge.evaluate_equivalence("q", "c", "a") is None


@pytest.mark.asyncio
async def test_judge_service_extract_core_query(mock_llm_provider, mock_cross_encoder):
    judge = JudgeService(mock_llm_provider, mock_cross_encoder)

    resp = ChatCompletionResponse(
        id="1",
        object="chat.completion",
        created=123,
        model="test",
        choices=[
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant", content="What is quantum computing?"
                ),
                finish_reason="stop",
            )
        ],
    )
    with patch.object(config, "CORE_QUERY_EXTRACTOR_MODEL", "qwen2.5-coder:3b"):
        mock_llm_provider.generate_ollama_completion = AsyncMock(return_value=resp)
        res = await judge._extract_core_query_with_llm(
            "Please explain quantum computing in 5 words"
        )
        assert res == "What is quantum computing?"

        mock_llm_provider.generate_ollama_completion = AsyncMock(
            side_effect=Exception("ollama error")
        )
        res_fallback = await judge._extract_core_query_with_llm("Original query")
        assert res_fallback == "Original query"


def test_judge_service_extract_text_from_json():
    judge = JudgeService(MagicMock(), MagicMock())
    assert (
        judge._extract_text_from_json('{"key": "hello", "val": "world"}')
        == "hello world"
    )
    assert (
        judge._extract_text_from_json('```json\n{"a": 1, "b": "two"}\n```') == "1 two"
    )
    assert judge._extract_text_from_json("plain text") == "plain text"


@pytest.mark.asyncio
async def test_judge_service_evaluate_local(mock_llm_provider, mock_cross_encoder):
    judge = JudgeService(mock_llm_provider, mock_cross_encoder)
    with patch.object(
        judge, "_extract_core_query_with_llm", AsyncMock(return_value="query")
    ):
        # Normal high entailment
        res = await judge.evaluate("query", "response")
        assert res["llm_relevance_score"] >= 4
        assert res["llm_contradiction_score"] == 0
        assert res["llm_entailment_score"] > 3.0

        # Contradiction case: contradiction logit is highest
        mock_cross_encoder.nli_predict_async = AsyncMock(
            return_value=[[10.0, -5.0, -5.0]]
        )
        res_contra = await judge.evaluate("query", "response")
        assert res_contra["llm_contradiction_score"] == 1
        assert res_contra["llm_entailment_score"] <= 2.0

        # InferenceUnavailable handled gracefully
        mock_cross_encoder.relevance_predict_async = AsyncMock(
            side_effect=InferenceUnavailable("down")
        )
        mock_cross_encoder.nli_predict_async = AsyncMock(
            side_effect=InferenceUnavailable("down")
        )
        res_down = await judge.evaluate("query", "response")
        assert "llm_relevance_score" in res_down

        # Unexpected outer exception
        with patch.object(
            judge, "_extract_text_from_json", side_effect=RuntimeError("fail")
        ):
            res_err = await judge.evaluate("query", "response")
            assert res_err == {}


def test_verifier_controller_rerank_endpoints():
    mock_cross_encoder = MagicMock(spec=CrossEncoderService)
    mock_cross_encoder.rerank_predict_async = AsyncMock(return_value=[0.2, 0.9])

    app.dependency_overrides[get_verifier_service] = lambda: mock_cross_encoder

    client = TestClient(app)
    response = client.post(
        "/api/v1/verifier/predict/rerank",
        json={"query": "test query", "candidates": ["candidate low", "candidate high"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    results = data["response"]["results"]
    assert results[0]["candidate"] == "candidate high"
    assert results[0]["score"] == 0.9

    # Test InferenceUnavailable 503
    mock_cross_encoder.rerank_predict_async = AsyncMock(
        side_effect=InferenceUnavailable("model offline")
    )
    response_503 = client.post(
        "/api/v1/verifier/predict/rerank",
        json={"query": "test query", "candidates": ["cand1"]},
    )
    assert response_503.status_code == 503
    assert "Inference engine unavailable" in response_503.json()["detail"]

    # Test default get_verifier_service
    app.dependency_overrides.pop(get_verifier_service, None)
    with patch(
        "app.core.dependencies.DependencyContainer.get_cross_encoder_service",
        return_value=mock_cross_encoder,
    ):
        assert get_verifier_service() == mock_cross_encoder


def test_cross_encoder_service_init_and_methods():
    from app.modules.inference.inference_client import (
        InferenceClient,
        InferenceConfigError,
    )

    # Disabled inference client raises InferenceConfigError
    mock_disabled_client = MagicMock(spec=InferenceClient)
    mock_disabled_client.enabled = False
    with pytest.raises(InferenceConfigError):
        CrossEncoderService(inference_client=mock_disabled_client)

    # Enabled inference client
    mock_client = MagicMock(spec=InferenceClient)
    mock_client.enabled = True
    mock_client.url_for.return_value = "http://localhost:8001"
    mock_client.rerank_sync.return_value = [0.8]
    mock_client.classify_sync.return_value = [[0.1, 0.9, 0.0]]
    mock_client.rerank = AsyncMock(return_value=[0.85])
    mock_client.classify = AsyncMock(return_value=[[0.2, 0.8, 0.0]])

    service = CrossEncoderService(inference_client=mock_client)

    # Empty inputs
    assert service.rerank_predict([]) == []
    assert service.relevance_predict("q", []) == []
    assert service.nli_predict("q", []) == []

    # Sync methods with flags enabled
    with patch.object(config, "USE_RERANKER_MODEL", True):
        assert service.rerank_predict([("q", "doc")]) == [0.8]
    with patch.object(config, "USE_RELEVANCE_MODEL", True):
        assert service.relevance_predict("q", ["doc"]) == [0.8]
    with patch.object(config, "USE_NLI_MODEL", True):
        assert service.nli_predict("q", ["doc"]) == [[0.1, 0.9, 0.0]]

    # Disabled flags raise InferenceUnavailable
    with patch.object(config, "USE_RERANKER_MODEL", False):
        with pytest.raises(InferenceUnavailable):
            service.rerank_predict([("q", "doc")])
    with patch.object(config, "USE_RELEVANCE_MODEL", False):
        with pytest.raises(InferenceUnavailable):
            service.relevance_predict("q", ["doc"])
    with patch.object(config, "USE_NLI_MODEL", False):
        with pytest.raises(InferenceUnavailable):
            service.nli_predict("q", ["doc"])


@pytest.mark.asyncio
async def test_cross_encoder_service_async_methods():
    from app.modules.inference.inference_client import InferenceClient

    mock_client = MagicMock(spec=InferenceClient)
    mock_client.enabled = True
    mock_client.url_for.return_value = "http://localhost:8001"
    mock_client.rerank = AsyncMock(return_value=[0.85])
    mock_client.classify = AsyncMock(return_value=[[0.2, 0.8, 0.0]])

    service = CrossEncoderService(inference_client=mock_client)

    # Empty inputs
    assert await service.rerank_predict_async([]) == []
    assert await service.relevance_predict_async("q", []) == []
    assert await service.nli_predict_async("q", []) == []

    # Async methods with flags enabled
    with patch.object(config, "USE_RERANKER_MODEL", True):
        assert await service.rerank_predict_async([("q", "doc")]) == [0.85]
    with patch.object(config, "USE_RELEVANCE_MODEL", True):
        assert await service.relevance_predict_async("q", ["doc"]) == [0.85]
    with patch.object(config, "USE_NLI_MODEL", True):
        assert await service.nli_predict_async("q", ["doc"]) == [[0.2, 0.8, 0.0]]

    # Disabled flags raise InferenceUnavailable
    with patch.object(config, "USE_RERANKER_MODEL", False):
        with pytest.raises(InferenceUnavailable):
            await service.rerank_predict_async([("q", "doc")])
    with patch.object(config, "USE_RELEVANCE_MODEL", False):
        with pytest.raises(InferenceUnavailable):
            await service.relevance_predict_async("q", ["doc"])
    with patch.object(config, "USE_NLI_MODEL", False):
        with pytest.raises(InferenceUnavailable):
            await service.nli_predict_async("q", ["doc"])
