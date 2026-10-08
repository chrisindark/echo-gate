import logging

from app.core.config import config
from app.modules.intent_classifier.intent_schema import IntentEnum

logger = logging.getLogger(__name__)


class CacheConfidenceEvaluator:
    """
    Evaluates whether a reranked candidate should be served from cache
    based on a dynamic confidence threshold.
    """

    @staticmethod
    def evaluate(
        rerank_score: float,
        request_temperature: float,
        request_intent: str | IntentEnum,
    ) -> bool:
        """
        Evaluate cache acceptance dynamically.

        Args:
            rerank_score: The composite score emitted by the RerankerService.
            request_temperature: The temperature requested by the user.
            request_intent: The classified intent of the user.

        Returns:
            True if the cache match is confident enough to be served.
        """
        intent_str = (
            request_intent.value
            if isinstance(request_intent, IntentEnum)
            else str(request_intent)
        )

        # Define confidence thresholds based on intent.
        # Factual/deterministic intents require HIGH thresholds because small prompt
        # changes (e.g., "before" vs "after") completely change the correct answer.
        # Subjective/casual intents can accept slightly lower scores.
        intent_thresholds = config.CACHE_CONFIDENCE_THRESHOLDS

        # Get thresholds for this intent, default to general if not found
        thresholds = intent_thresholds.get(
            intent_str,
            intent_thresholds.get("general_query", {"high": 0.95, "moderate": 0.88}),
        )
        high_threshold = thresholds["high"]
        moderate_threshold = thresholds["moderate"]

        logger.debug(
            f"Intent: {intent_str}, High threshold: {high_threshold}, Moderate threshold: {moderate_threshold}, Request Temperature: {request_temperature}"
        )

        # 1. Absolute High Confidence Match
        if rerank_score >= high_threshold:
            return True

        # 2. Moderate Confidence Match (Requires Risk Evaluation)
        if moderate_threshold <= rerank_score < high_threshold:
            # High creativity requested -> Reject moderate cache match (user wants variety)
            if request_temperature > 1.6:
                return False

            # Highly factual or deterministic request -> Reject moderate match
            # Factual queries require extreme precision. A moderate match might represent
            # a changed number, negation, or specific entity. We must demand a HIGH match.
            if request_temperature < 0.6:
                return False

            # Middle-ground temperature.
            # Only allow moderate matches for intents where slight divergence is acceptable.
            subjective_intents = {
                IntentEnum.CHITCHAT.value,
                IntentEnum.GREETING.value,
            }
            return intent_str in subjective_intents

        # 3. Low Confidence Match -> Always reject
        return False
