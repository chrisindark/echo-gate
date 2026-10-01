import hashlib
import json
import logging

from app.core.config import INTENT_CLASSIFIER_MODEL, INTENT_CLASSIFIER_SERVICE
from app.modules.chat.chat_schema import ChatCompletionRequest, ChatMessage
from app.modules.intent_classifier.intent_schema import (
    IntentClassificationResult,
    IntentEnum,
)
from app.modules.llm.llm_provider_service import LlmProviderService

logger = logging.getLogger(__name__)


class IntentClassifierService:
    def __init__(self, llm_provider_service: LlmProviderService):
        self.service_name = INTENT_CLASSIFIER_SERVICE
        self.model = INTENT_CLASSIFIER_MODEL
        self.llm_provider_service = llm_provider_service

    def _generate_cache_key(self, request: ChatCompletionRequest) -> str:
        key_dict = {
            "service_name": request.service_name,
            "model": request.model,
            "temperature": request.temperature,
            "user_id": request.user_id,
            "tenant_id": request.tenant_id,
            "session_id": request.session_id,
            "conversation_id": request.conversation_id,
            "messages": [
                {"role": msg.role, "content": msg.content} for msg in request.messages
            ],
        }
        key_str = json.dumps(key_dict, sort_keys=True)
        return hashlib.sha256(key_str.encode()).hexdigest()

    async def classify_intent(
        self, request: ChatCompletionRequest
    ) -> IntentClassificationResult:
        prompt_text = "\n".join(
            [f"{msg.role}: {msg.content}" for msg in request.messages]
        )
        prompt_text = f"model:{request.model}|{prompt_text}"
        enums_list = [e.value for e in IntentEnum]

        system_msg = (
            "You are an expert intent classifier. "
            "Categorize the user's prompt into exactly one of the following categories: "
            f"{', '.join(enums_list)}. "
            "Also evaluate the time_sensitivity of the prompt, providing a score between 0.0 (static, unchanging knowledge) "
            "and 1.0 (highly time-sensitive, like current stock prices, weather, or real-time news). "
            "Respond ONLY with a valid JSON object containing the keys 'intent' and 'time_sensitivity'."
        )
        exact_hash = hashlib.sha256(prompt_text.encode()).hexdigest()

        request = ChatCompletionRequest(
            service_name=self.service_name,
            model=self.model,
            messages=[
                ChatMessage(role="system", content=system_msg),
                ChatMessage(role="user", content=prompt_text),
            ],
            temperature=0.0,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "intent_classification",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "intent": {"type": "string", "enum": enums_list},
                            "time_sensitivity": {"type": "number"},
                        },
                        "required": ["intent", "time_sensitivity"],
                    },
                },
            },
        )

        try:
            response = await self.llm_provider_service.generate_ollama_completion(
                request
            )
            logger.info(
                f"LLM classifier service generated response successfully for {exact_hash}"
            )
            content = response.choices[0].message.content if response.choices else ""
            data = json.loads(content)

            intent_value = data.get("intent", IntentEnum.GENERAL_QUERY.value)
            time_sensitivity = float(data.get("time_sensitivity", 0.0))

            return IntentClassificationResult(
                intent=IntentEnum(intent_value), time_sensitivity=time_sensitivity
            )
        except Exception as e:
            logger.error(f"Error classifying intent: {e}")
            return IntentClassificationResult(
                intent=IntentEnum.GENERAL_QUERY, time_sensitivity=0.0
            )

    def calculate_ttl(self, intent: IntentEnum, time_sensitivity: float) -> int | None:
        """
        Calculate TTL (Time To Live) in seconds based on intent and temporal sensitivity.
        Returns None if the content should not be cached.
        """
        if time_sensitivity >= 0.9 or intent == IntentEnum.FINANCE_MARKET:
            # Don't cache live data (stocks, highly temporal queries)
            return None

        if intent == IntentEnum.WEATHER:
            return 5 * 60  # 5 minutes

        if intent == IntentEnum.PRODUCT_INFO:
            return 60 * 60  # 1 hour

        if intent in [
            IntentEnum.CODE_GENERATION,
            IntentEnum.CODE_EXPLANATION,
            IntentEnum.DEBUGGING,
            IntentEnum.ERROR_ANALYSIS,
            IntentEnum.REFACTORING,
            IntentEnum.OPTIMIZATION,
            IntentEnum.TEST_GENERATION,
        ]:
            return 24 * 60 * 60  # 1 day

        # Static knowledge and default general queries
        return 7 * 24 * 60 * 60  # 7 days
