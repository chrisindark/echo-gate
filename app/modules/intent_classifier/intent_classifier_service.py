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

    def _generate_cache_key(self, request: ChatCompletionRequest) -> str:
        key_dict = {
            "service_name": request.service_name,
            "model": request.model,
            "temperature": request.temperature,
            "tenant_id": request.user or "anonymous",
            "messages": [
                {"role": msg.role, "content": msg.content} for msg in request.messages
            ],
        }
        key_str = json.dumps(key_dict, sort_keys=True)
        return hashlib.sha256(key_str.encode()).hexdigest()

    async def classify_intent(self, request: ChatCompletionRequest) -> IntentEnum:
        prompt_text = "\n".join(
            [f"{msg.role}: {msg.content}" for msg in request.messages]
        )
        prompt_text = f"model:{request.model}|{prompt_text}"
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
                f"LLM classifier service generated response successfully for {exact_hash}"
            )
            content = response.choices[0].message.content if response.choices else ""
            data = json.loads(content)
            intent_value = data.get("intent", IntentEnum.GENERAL_QUERY.value)
            return IntentEnum(intent_value)
        except Exception as e:
            logger.error(f"Error classifying intent: {e}")
            return IntentEnum.GENERAL_QUERY
