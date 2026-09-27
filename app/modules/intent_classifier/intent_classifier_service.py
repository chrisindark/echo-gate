import hashlib
import json
import logging

from app.modules.chat.chat_schema import ChatCompletionRequest, ChatMessage
from app.modules.intent_classifier.intent_schema import IntentEnum
from app.modules.llm.llm_provider_service import LlmProviderService

logger = logging.getLogger(__name__)


class IntentClassifierService:
    def __init__(self, llm_provider_service: LlmProviderService):
        self.service_name = "ollama"
        self.model = "qwen2.5-coder:1.5b"  # default fast model
        self.llm_provider_service = llm_provider_service

    async def classify_intent(self, prompt_text: str) -> IntentEnum:
        enums_list = [e.value for e in IntentEnum]

        system_msg = (
            "You are an expert intent classifier. "
            "Categorize the user's prompt into exactly one of the following categories: "
            f"{', '.join(enums_list)}. "
            "Respond ONLY with a valid JSON object with a single key 'intent' containing the category."
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
                            "intent": {"type": "string", "enum": enums_list}
                        },
                        "required": ["intent"],
                    },
                },
            },
        )

        try:
            response = await self.llm_provider_service.generate_ollama_completion(
                request
            )
            logger.info(
                f"LLM provider service generated response successfully for {exact_hash}"
            )
            content = response.choices[0].message.content
            data = json.loads(content)
            intent_value = data.get("intent", IntentEnum.GENERAL_QUERY.value)
            return IntentEnum(intent_value)
        except Exception as e:
            logger.error(f"Error classifying intent: {e}")
            return IntentEnum.GENERAL_QUERY
