import unittest

from app.core.config import config
from app.modules.reranking.reranker_service import RerankerService
from app.modules.verifier.instruction_service import InstructionVerifier


class _FakeCrossEncoder:
    """Returns pre-scripted scores and records the pairs it was asked to score."""

    def __init__(self, scores: list[float]):
        self.scores = scores
        self.pairs: list[tuple[str, str]] = []

    def rerank_predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        self.pairs = list(pairs)
        return self.scores


def _candidate(
    candidate_id: str,
    *,
    user_prompt: str | None = None,
    prompt: str | None = None,
    model: str | None = None,
    service_name: str | None = None,
    intent: str | None = None,
    core_operation: str | None = None,
    core_subject: str | None = None,
    subject_modifier: str | None = None,
    action_modifier: str | None = None,
    entities: list[str] | None = None,
    system_prompt: str | None = None,
    temperature: float = 0.0,
) -> dict:
    return {
        "id": candidate_id,
        "score": 1.0,
        "payload": {
            "prompt": prompt
            if prompt is not None
            else f"model:gemini-3.1-flash-lite|user: {user_prompt or ''}",
            "user_prompt": user_prompt,
            "system_prompt": system_prompt,
            "model": model,
            "service_name": service_name,
            "intent": intent,
            "core_operation": core_operation,
            "core_subject": core_subject,
            "subject_modifier": subject_modifier,
            "action_modifier": action_modifier,
            "entities": entities or [],
            "temperature": temperature,
            "response": {"choices": [{"message": {"content": "cached"}}]},
        },
    }


class TestRerankerService(unittest.TestCase):
    def setUp(self):
        self._original_mode = config.RERANKER_SCORING_MODE
        config.RERANKER_SCORING_MODE = "multiplicative"

    def tearDown(self):
        config.RERANKER_SCORING_MODE = self._original_mode

    def _service(
        self, scores: list[float]
    ) -> tuple[RerankerService, _FakeCrossEncoder]:
        cross_encoder = _FakeCrossEncoder(scores)
        service = RerankerService(cross_encoder, InstructionVerifier())
        return service, cross_encoder

    def test_verify_pairs_user_to_user_and_strips_model_prefix(self):
        service, cross_encoder = self._service([0.5])
        candidates = [_candidate("a", user_prompt="cached question")]

        service.verify(
            query="model:gemini-3.1-flash-lite|user: current question",
            user_query="current question",
            candidates=candidates,
        )

        self.assertEqual(cross_encoder.pairs, [("current question", "cached question")])

    def test_verify_falls_back_to_cleaned_full_prompt(self):
        service, cross_encoder = self._service([0.5])
        candidates = [
            {
                "id": "a",
                "payload": {
                    "prompt": "model:gemini-3.1-flash-lite|user: cached question",
                },
            }
        ]

        service.verify(
            query="model:gemini-3.1-flash-lite|user: current question",
            user_query=None,
            candidates=candidates,
        )

        self.assertEqual(
            cross_encoder.pairs, [("user: current question", "user: cached question")]
        )

    def test_multiplicative_keeps_strong_match_above_threshold(self):
        # base 0.95 with a single metadata mismatch (factor 0.95) -> ~0.90.
        service, _ = self._service([0.95])
        candidates = [_candidate("a", user_prompt="q", core_subject="reversing")]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            request_core_subject="reverse",
            user_query="q",
        )

        score = reranked[0]["rerank_score"]
        self.assertGreater(score, 0.85)
        self.assertEqual(
            reranked[0]["score_breakdown"]["details"]["metadata_mismatches"],
            ["core_subject"],
        )

    def test_multiplicative_never_collapses_a_strong_match_to_zero(self):
        # Service + intent mismatch and a large temperature delta would have
        # cost 0.15 + 0.02 + 0.20 = 0.37 under the old additive model.
        service, _ = self._service([0.99])
        candidates = [
            _candidate(
                "a",
                user_prompt="q",
                service_name="openai",
                intent="chitchat",
                temperature=2.7,
            )
        ]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            request_service="google-genai",
            request_intent="code_generation",
            request_temperature=0.7,
            user_query="q",
        )

        self.assertGreater(reranked[0]["rerank_score"], 0.8)

    def test_intent_factor_skipped_when_request_intent_unknown(self):
        service, _ = self._service([0.9])
        candidates = [_candidate("a", user_prompt="q", intent="chitchat")]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            request_intent=None,
            user_query="q",
        )

        factors = reranked[0]["score_breakdown"]["factors"]
        self.assertEqual(factors["intent"], 1.0)
        self.assertAlmostEqual(reranked[0]["rerank_score"], 0.9, places=3)

    def test_entity_conflict_is_penalized(self):
        service, _ = self._service([0.95])
        candidates = [_candidate("a", user_prompt="q", entities=["VERSION:3.12"])]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            request_entities=["VERSION:3.11"],
            user_query="q",
        )

        breakdown = reranked[0]["score_breakdown"]
        self.assertTrue(breakdown["details"]["entity_conflict"])
        self.assertEqual(breakdown["factors"]["entities"], 0.85)
        self.assertAlmostEqual(reranked[0]["rerank_score"], 0.95 * 0.85, places=4)

    def test_entity_overlap_is_a_bounded_boost(self):
        service, _ = self._service([0.9])
        candidates = [_candidate("a", user_prompt="q", entities=["NUMBER:5"])]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            request_entities=["NUMBER:5"],
            user_query="q",
        )

        factors = reranked[0]["score_breakdown"]["factors"]
        self.assertAlmostEqual(factors["entities"], 1.05, places=4)
        self.assertGreater(reranked[0]["rerank_score"], 0.9)

    def test_metadata_is_not_penalized_when_request_field_missing(self):
        service, _ = self._service([0.9])
        candidates = [_candidate("a", user_prompt="q", core_subject="python")]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            request_core_subject=None,
            user_query="q",
        )

        self.assertEqual(
            reranked[0]["score_breakdown"]["details"]["metadata_mismatches"], []
        )
        self.assertEqual(reranked[0]["score_breakdown"]["factors"]["metadata"], 1.0)

    def test_metadata_is_not_penalized_when_candidate_field_missing(self):
        # Legacy point: request extracted "ask" but the candidate has no
        # core_operation stored. That is not evidence of a mismatch.
        service, _ = self._service([0.9])
        candidates = [_candidate("a", user_prompt="q", core_operation=None)]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            request_core_operation="ask",
            user_query="q",
        )

        self.assertEqual(
            reranked[0]["score_breakdown"]["details"]["metadata_mismatches"], []
        )
        self.assertEqual(reranked[0]["score_breakdown"]["factors"]["metadata"], 1.0)

    def test_metadata_possessive_is_not_a_mismatch(self):
        # "France's capital city" vs "France capital" differ only by the
        # possessive/punctuation, so they must not register as a mismatch.
        service, _ = self._service([0.99])
        candidates = [_candidate("a", user_prompt="q", core_subject="France capital")]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            request_core_subject="France's capital city",
            user_query="q",
        )

        self.assertEqual(
            reranked[0]["score_breakdown"]["details"]["metadata_mismatches"], []
        )
        self.assertEqual(reranked[0]["score_breakdown"]["factors"]["metadata"], 1.0)

    def test_system_prompt_not_penalized_when_candidate_missing(self):
        # Legacy point stored without a system prompt must not be penalized when
        # the request carries one.
        service, _ = self._service([0.99])
        candidates = [_candidate("a", user_prompt="q", system_prompt=None)]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            system_query="You are a helpful assistant",
            user_query="q",
        )

        self.assertEqual(
            reranked[0]["score_breakdown"]["factors"]["system_prompt"], 1.0
        )
        self.assertAlmostEqual(reranked[0]["rerank_score"], 0.99, places=4)

    def test_breakdown_contains_expected_keys(self):
        service, _ = self._service([0.8])
        candidates = [_candidate("a", user_prompt="q")]

        reranked = service.rerank(query="q", candidates=candidates, user_query="q")
        breakdown = reranked[0]["score_breakdown"]

        self.assertEqual(breakdown["scoring_mode"], "multiplicative")
        self.assertIn("base_score", breakdown)
        self.assertIn("final_score", breakdown)
        self.assertIn("details", breakdown)
        for key in (
            "model",
            "service",
            "intent",
            "metadata",
            "entities",
            "system_prompt",
            "temperature",
            "instruction",
        ):
            self.assertIn(key, breakdown["factors"])

    def test_ordering_is_preserved(self):
        # The first candidate has no penalties; the second a metadata mismatch.
        service, _ = self._service([0.9, 0.9])
        candidates = [
            _candidate("a", user_prompt="q", core_subject="reverse"),
            _candidate("b", user_prompt="q", core_subject="sorting"),
        ]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            request_core_subject="reverse",
            user_query="q",
        )

        self.assertEqual(reranked[0]["id"], "a")
        self.assertGreater(reranked[0]["rerank_score"], reranked[1]["rerank_score"])

    def test_additive_mode_is_selectable(self):
        config.RERANKER_SCORING_MODE = "additive"
        service, _ = self._service([0.9])
        candidates = [_candidate("a", user_prompt="q", intent="chitchat")]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            request_intent="code_generation",
            user_query="q",
        )

        breakdown = reranked[0]["score_breakdown"]
        self.assertEqual(breakdown["scoring_mode"], "additive")
        # 1.0 (intent factor) - 0.08 = base - 0.08.
        self.assertAlmostEqual(reranked[0]["rerank_score"], 0.82, places=4)

    def test_returns_empty_for_no_candidates(self):
        service, _ = self._service([])
        self.assertEqual(service.rerank(query="q", candidates=[], user_query="q"), [])

    def test_system_prompt_mismatch_is_bounded(self):
        service, _ = self._service([0.9])
        candidates = [
            _candidate("a", user_prompt="q", system_prompt="Completely different text")
        ]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            system_query="You are a helpful assistant",
            user_query="q",
        )

        factor = reranked[0]["score_breakdown"]["factors"]["system_prompt"]
        self.assertGreaterEqual(factor, 0.90)
        self.assertLess(factor, 1.0)

    def test_metadata_comparisons_record_both_sides(self):
        # The trace must let a near-boundary rejection be reconstructed, which
        # requires the candidate-side value and the overlap, not just the field
        # name in `metadata_mismatches`.
        service, _ = self._service([0.99])
        candidates = [
            _candidate("a", user_prompt="q", core_subject="capital of france")
        ]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            request_core_subject="France capital city",
            user_query="q",
        )

        comparisons = reranked[0]["score_breakdown"]["details"]["metadata_comparisons"]
        subject = comparisons["core_subject"]
        self.assertTrue(subject["judged"])
        self.assertEqual(subject["request"], "France capital city")
        self.assertEqual(subject["candidate"], "capital of france")
        self.assertIn("overlap", subject)
        self.assertIn("mismatch", subject)

    def test_metadata_comparison_skip_reasons_are_recorded(self):
        service, _ = self._service([0.9])
        candidates = [_candidate("a", user_prompt="q", core_operation=None)]

        reranked = service.rerank(
            query="q",
            candidates=candidates,
            request_core_operation="ask",
            user_query="q",
        )

        comparisons = reranked[0]["score_breakdown"]["details"]["metadata_comparisons"]
        self.assertEqual(
            comparisons["core_operation"],
            {"judged": False, "reason": "candidate_empty", "request": "ask"},
        )
        # A field the request never carried is not judged either.
        self.assertEqual(
            comparisons["core_subject"],
            {"judged": False, "reason": "request_empty"},
        )


if __name__ == "__main__":
    unittest.main()
