import logging

logger = logging.getLogger(__name__)


class CacheConfidenceEvaluator:
    """
    Evaluates whether a reranked candidate should be served from cache
    based on a dynamic confidence threshold.
    """

    @staticmethod
    def evaluate(
        rerank_score: float, request_temperature: float, request_intent: str
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
        # Define confidence thresholds based on intent.
        # Factual/deterministic intents require HIGH thresholds because small prompt
        # changes (e.g., "before" vs "after") completely change the correct answer.
        # Subjective/casual intents can accept slightly lower scores.
        intent_thresholds = {
            "code_generation": {"high": 0.95, "moderate": 0.88},
            "casual_chat": {"high": 0.85, "moderate": 0.75},
            "general_query": {"high": 0.95, "moderate": 0.88},
            "finance_market": {"high": 0.98, "moderate": 0.92},
            "weather": {"high": 0.95, "moderate": 0.88},
            "product_info": {"high": 0.95, "moderate": 0.88},
            "debugging": {"high": 0.95, "moderate": 0.88},
            "error_analysis": {"high": 0.95, "moderate": 0.88},
            "code_explanation": {"high": 0.94, "moderate": 0.85},
            "code_review": {"high": 0.94, "moderate": 0.85},
            "data_extraction": {"high": 0.96, "moderate": 0.90},
            "research": {"high": 0.95, "moderate": 0.88},
            "refactoring": {"high": 0.94, "moderate": 0.85},
            "optimization": {"high": 0.94, "moderate": 0.85},
            "test_generation": {"high": 0.94, "moderate": 0.85},
            "code_comparison": {"high": 0.95, "moderate": 0.88},
        }

        # Get thresholds for this intent, default to general if not found
        thresholds = intent_thresholds.get(
            request_intent, intent_thresholds["general_query"]
        )
        high_threshold = thresholds["high"]
        moderate_threshold = thresholds["moderate"]

        logger.info(
            f"Intent: {request_intent}, High threshold: {high_threshold}, Moderate threshold: {moderate_threshold}, Request Temperature: {request_temperature}"
        )

        # 1. Absolute High Confidence Match
        if rerank_score >= high_threshold:
            return True

        # 2. Moderate Confidence Match (Requires Risk Evaluation)
        if moderate_threshold <= rerank_score < high_threshold:
            # High creativity requested -> Reject moderate cache match (user wants variety)
            if request_temperature > 0.8:
                return True

            # Highly factual or deterministic request -> Reject moderate match
            # Factual queries require extreme precision. A moderate match might represent
            # a changed number, negation, or specific entity. We must demand a HIGH match.
            if request_temperature < 0.3:
                return False

            # Middle-ground temperature.
            # Only allow moderate matches for intents where slight divergence is acceptable.
            subjective_intents = {
                "casual_chat",
                "brainstorming",
                "creative_writing",
            }
            return request_intent in subjective_intents

        # 3. Low Confidence Match -> Always reject
        return False
