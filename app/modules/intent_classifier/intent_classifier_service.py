import json
import logging

from app.core.config import config
from app.core.logger import log_latency
from app.modules.chat.chat_schema import ChatCompletionRequest, ChatMessage
from app.modules.intent_classifier.intent_schema import (
    IntentClassificationResult,
    IntentEnum,
)
from app.modules.llm.llm_provider_service import LlmProviderService

logger = logging.getLogger(__name__)


class IntentClassifierService:
    def __init__(
        self,
        llm_provider_service: LlmProviderService,
        model: str | None = None,
        service: str | None = None,
    ):
        self.service_name = service or config.INTENT_CLASSIFIER_SERVICE
        self.model = model or config.INTENT_CLASSIFIER_MODEL
        self.llm_provider_service = llm_provider_service

    @log_latency()
    async def classify_intent(self, request: ChatCompletionRequest):
        prompt_text = "\n".join(
            [f"{msg.role}: {msg.content}" for msg in request.messages]
        )
        prompt_text = f"model:{request.model}|{prompt_text}"
        enums_list = [e.value for e in IntentEnum if e != IntentEnum.EMPTY and e.value]

        system_msg = (
            "You are an expert intent classifier and semantic analyzer. "
            "Categorize the user's prompt into exactly one of the following categories: "
            f"{', '.join(enums_list)}.\n\n"
            "Follow these instructions strictly to analyze the query:\n"
            "1. Evaluate the `time_sensitivity` of the prompt, providing a score between 0.0 (static, unchanging knowledge) "
            "and 1.0 (highly time-sensitive or real-time like current stock prices, weather).\n"
            "2. Identify the `core_operation` or action verb of the query (e.g. 'book', 'cancel', 'sort', 'delete', 'compare'). "
            "Limit to a maximum of 2 words.\n"
            "3. Identify the `core_subject`, phrase or noun being acted upon (e.g. 'flight ticket', 'docker container', 'apple stock'). "
            "Limit to a maximum of 4 words.\n"
            "4. Extract the `subject_modifier` (the specific attribute, quality, or constraint applied to the subject, e.g. 'cheapest' for flights, 'latest' for news, 'secure' for connection). Return null if there is no clear modifier.\n"
            "5. Extract the `action_modifier` (the style, method, or parameters defining how the operation should be performed, e.g. 'alphabetically', 'silently', 'quickly'). Return null if there is no specific manner defined.\n\n"
            "Respond ONLY with a valid JSON object containing the keys: "
            "'intent', 'time_sensitivity', 'core_operation', 'core_subject',"
            "'subject_modifier', 'action_modifier'"
        )

        request = ChatCompletionRequest(
            service_name=self.service_name,
            model=self.model,
            messages=[
                ChatMessage(role="system", content=system_msg),
                ChatMessage(role="user", content=prompt_text),
            ],
            temperature=config.INTENT_CLASSIFIER_TEMPERATURE,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "intent_classification",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "intent": {"type": "string", "enum": enums_list},
                            "time_sensitivity": {"type": "number"},
                            "core_operation": {"type": "string"},
                            "core_subject": {"type": "string"},
                            "subject_modifier": {"type": ["string", "null"]},
                            "action_modifier": {"type": ["string", "null"]},
                        },
                        "required": [
                            "intent",
                            "time_sensitivity",
                            "core_operation",
                            "core_subject",
                            "subject_modifier",
                            "action_modifier",
                        ],
                    },
                },
            },
        )

        try:
            response = await self.llm_provider_service.generate_ollama_completion(
                request
            )
            logger.info("LLM classifier service generated response successfully")
            content = response.choices[0].message.content if response.choices else ""
            data = json.loads(content)

            intent_value = data.get("intent", IntentEnum.GENERAL_QUERY.value)
            time_sensitivity = float(data.get("time_sensitivity", 0.0))
            core_operation = data.get("core_operation", "")
            core_subject = data.get("core_subject", "")
            subject_modifier = data.get("subject_modifier", None)
            action_modifier = data.get("action_modifier", None)

            try:
                parsed_intent = IntentEnum(intent_value)
                if parsed_intent == IntentEnum.EMPTY:
                    parsed_intent = IntentEnum.GENERAL_QUERY
            except ValueError:
                parsed_intent = IntentEnum.GENERAL_QUERY

            return IntentClassificationResult(
                intent=parsed_intent,
                time_sensitivity=time_sensitivity,
                core_operation=core_operation,
                core_subject=core_subject,
                subject_modifier=subject_modifier,
                action_modifier=action_modifier,
            )
        except Exception:
            logger.exception("Error classifying intent")
            return IntentClassificationResult(
                intent=IntentEnum.GENERAL_QUERY,
                time_sensitivity=0.0,
                core_operation="",
                core_subject="",
                subject_modifier=None,
                action_modifier=None,
            )

    def calculate_ttl(self, intent: IntentEnum, time_sensitivity: float) -> int | None:
        """
        Calculate TTL (Time To Live) in seconds based on intent and temporal sensitivity.
        Returns None if the content should not be cached.
        """
        if time_sensitivity >= 0.9 or intent == IntentEnum.FINANCE_MARKET:
            # Don't cache live data (stocks, highly temporal queries)
            return None

        # Fetch TTL from config based on intent string value
        return config.INTENT_TTL_SECONDS.get(
            intent.value,
            config.INTENT_TTL_SECONDS.get("general_query", 7 * 24 * 60 * 60),
        )
