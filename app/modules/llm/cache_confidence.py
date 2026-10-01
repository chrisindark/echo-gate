class CacheConfidenceEvaluator:
    """
    Evaluates whether a reranked candidate should be served from cache
    based on a dynamic confidence threshold.
    """

    @staticmethod
    def evaluate(
        final_score: float, request_temperature: float, request_intent: str
    ) -> bool:
        """
        Evaluate cache acceptance dynamically.

        Args:
            final_score: The composite score emitted by the RerankerService.
            request_temperature: The temperature requested by the user.
            request_intent: The classified intent of the user.

        Returns:
            True if the cache match is confident enough to be served.
        """
        # Define confidence thresholds based on intent
        # High-risk intents (creative/subjective) need higher scores
        # Low-risk intents (factual/deterministic) can accept lower scores
        intent_thresholds = {
            "code_generation": {"high": 0.95, "moderate": 0.88},
            "casual_chat": {"high": 0.85, "moderate": 0.70},
            "general_query": {"high": 0.90, "moderate": 0.75},
            "finance_market": {"high": 0.98, "moderate": 0.90},
            "weather": {"high": 0.95, "moderate": 0.85},
            "product_info": {"high": 0.92, "moderate": 0.80},
            "debugging": {"high": 0.93, "moderate": 0.82},
            "error_analysis": {"high": 0.93, "moderate": 0.82},
            "code_explanation": {"high": 0.91, "moderate": 0.78},
            "code_review": {"high": 0.94, "moderate": 0.85},
            "data_extraction": {"high": 0.96, "moderate": 0.88},
            "research": {"high": 0.92, "moderate": 0.80},
            "refactoring": {"high": 0.94, "moderate": 0.84},
            "optimization": {"high": 0.94, "moderate": 0.84},
            "test_generation": {"high": 0.93, "moderate": 0.83},
            "code_comparison": {"high": 0.94, "moderate": 0.84},
        }

        # Get thresholds for this intent, default to general if not found
        thresholds = intent_thresholds.get(
            request_intent, intent_thresholds["general_query"]
        )
        high_threshold = thresholds["high"]
        moderate_threshold = thresholds["moderate"]

        # 1. Absolute High Confidence Match
        if final_score >= high_threshold:
            return True

        # 2. Moderate Confidence Match (Requires Risk Evaluation)
        if moderate_threshold <= final_score < high_threshold:
            # High creativity requested -> Reject moderate cache match
            if request_temperature > 0.8:
                return False

            # Highly factual or deterministic request -> Accept moderate match
            if request_temperature < 0.3:
                return True

            # Middle-ground temperature. Default to false to be safe unless it's a known factual intent
            factual_intents = {
                "general_query",
                "data_extraction",
                "code_explanation",
                "weather",
                "product_info",
                "finance_market",
            }
            return request_intent in factual_intents

        # 3. Low Confidence Match -> Always reject
        return False
