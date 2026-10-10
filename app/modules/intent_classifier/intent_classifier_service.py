import asyncio
import json
import logging
from collections import OrderedDict

from fastapi import HTTPException

from app.core.config import config
from app.core.logger import log_latency
from app.core.retry import execute_with_retry
from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
)
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
        timeout_seconds: float | None = None,
        max_tokens: int | None = None,
        max_retries: int | None = None,
        reasoning_effort: str | None = None,
        cache_size: int = 1000,
    ):
        self.service_name = service or config.INTENT_CLASSIFIER_SERVICE
        self.model = model or config.INTENT_CLASSIFIER_MODEL
        self.llm_provider_service = llm_provider_service
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else config.INTENT_CLASSIFIER_TIMEOUT_SECONDS
        )
        self.max_tokens = (
            max_tokens
            if max_tokens is not None
            else config.INTENT_CLASSIFIER_MAX_TOKENS
        )
        self.max_retries = (
            max_retries
            if max_retries is not None
            else config.INTENT_CLASSIFIER_MAX_RETRIES
        )
        self.reasoning_effort = (
            reasoning_effort
            if reasoning_effort is not None
            else getattr(config, "INTENT_CLASSIFIER_REASONING_EFFORT", "low")
        )
        self._cache: OrderedDict[str, IntentClassificationResult | None] = OrderedDict()
        self._max_cache_size: int = cache_size

    def clear_cache(self) -> None:
        self._cache.clear()

    @staticmethod
    def _clean_and_parse_json(content: str) -> dict:
        if not content:
            return {}
        clean_text = content.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:].strip()
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:].strip()
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3].strip()

        try:
            return json.loads(clean_text)
        except json.JSONDecodeError:
            start = clean_text.find("{")
            end = clean_text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(clean_text[start : end + 1])
            raise

    async def _execute_llm_request(
        self, classification_request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        try:
            return await self.llm_provider_service.generate_completion(
                classification_request, service_name=self.service_name
            )
        except HTTPException as e:
            # If the provider rejected json_schema, fallback to json_object mode
            if (
                e.status_code == 400
                and classification_request.response_format
                and classification_request.response_format.get("type") == "json_schema"
            ):
                logger.warning(
                    f"Intent classifier call with json_schema failed for {self.service_name} ({e.detail}). "
                    f"Retrying with json_object format..."
                )
                retry_request = classification_request.model_copy(
                    update={"response_format": {"type": "json_object"}}
                )
                try:
                    return await self.llm_provider_service.generate_completion(
                        retry_request, service_name=self.service_name
                    )
                except HTTPException as e2:
                    if e2.status_code == 400:
                        logger.warning(
                            f"Intent classifier call with json_object failed for {self.service_name} ({e2.detail}). "
                            f"Retrying without response_format constraint..."
                        )
                        fallback_request = retry_request.model_copy(
                            update={"response_format": None}
                        )
                        return await self.llm_provider_service.generate_completion(
                            fallback_request, service_name=self.service_name
                        )
                    raise
            elif (
                e.status_code == 400
                and classification_request.response_format
                and classification_request.response_format.get("type") == "json_object"
            ):
                logger.warning(
                    f"Intent classifier call with json_object failed for {self.service_name} ({e.detail}). "
                    f"Retrying without response_format constraint..."
                )
                fallback_request = classification_request.model_copy(
                    update={"response_format": None}
                )
                return await self.llm_provider_service.generate_completion(
                    fallback_request, service_name=self.service_name
                )
            raise

    @log_latency()
    async def classify_intent(
        self, request: ChatCompletionRequest
    ) -> IntentClassificationResult | None:
        cache_key = None
        try:
            prompt_text = "\n".join(
                [f"{msg.role}: {msg.content}" for msg in request.messages]
            )
            prompt_text = f"model:{request.model}|{prompt_text}"
            cache_key = f"svc:{self.service_name}|model:{self.model}|{prompt_text}"

            if cache_key in self._cache:
                logger.info("Reusing cached intent classification for prompt")
                cached = self._cache[cache_key]
                self._cache.move_to_end(cache_key)
                return cached

            enums_list = [
                e.value for e in IntentEnum if e != IntentEnum.EMPTY and e.value
            ]

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

            is_reasoning_model = any(
                kw in self.model.lower()
                for kw in ["gpt-oss", "o1", "o3", "deepseek-r1", "qwq"]
            )
            reasoning_effort = self.reasoning_effort if is_reasoning_model else None

            classification_request = ChatCompletionRequest(
                service_name=self.service_name,
                model=self.model,
                messages=[
                    ChatMessage(role="system", content=system_msg),
                    ChatMessage(role="user", content=prompt_text),
                ],
                temperature=config.INTENT_CLASSIFIER_TEMPERATURE,
                max_tokens=self.max_tokens,
                reasoning_effort=reasoning_effort,
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

            response = await execute_with_retry(
                self._execute_llm_request,
                classification_request,
                timeout_seconds=self.timeout_seconds,
                max_retries=self.max_retries,
            )

            logger.info(
                f"LLM classifier service ({self.service_name}) generated response successfully"
            )
            content = response.choices[0].message.content if response.choices else ""
            data = self._clean_and_parse_json(content)

            intent_value = data.get("intent", IntentEnum.GENERAL_QUERY.value)
            try:
                time_sensitivity = float(data.get("time_sensitivity", 0.0))
            except (ValueError, TypeError):
                time_sensitivity = 0.0
            time_sensitivity = max(0.0, min(1.0, time_sensitivity))

            core_operation = str(data.get("core_operation") or "").strip()
            if core_operation.lower() in ("null", "none", "n/a", "undefined"):
                core_operation = ""

            core_subject = str(data.get("core_subject") or "").strip()
            if core_subject.lower() in ("null", "none", "n/a", "undefined"):
                core_subject = ""

            subject_modifier = data.get("subject_modifier")
            if subject_modifier is not None:
                subject_modifier_str = str(subject_modifier).strip()
                if subject_modifier_str.lower() in (
                    "null",
                    "none",
                    "n/a",
                    "undefined",
                    "",
                ):
                    subject_modifier = None
                else:
                    subject_modifier = subject_modifier_str

            action_modifier = data.get("action_modifier")
            if action_modifier is not None:
                action_modifier_str = str(action_modifier).strip()
                if action_modifier_str.lower() in (
                    "null",
                    "none",
                    "n/a",
                    "undefined",
                    "",
                ):
                    action_modifier = None
                else:
                    action_modifier = action_modifier_str

            try:
                if isinstance(intent_value, str):
                    intent_value = intent_value.strip().lower()
                parsed_intent = IntentEnum(intent_value)
                if parsed_intent == IntentEnum.EMPTY:
                    parsed_intent = IntentEnum.GENERAL_QUERY
            except (ValueError, KeyError):
                parsed_intent = IntentEnum.GENERAL_QUERY

            result = IntentClassificationResult(
                intent=parsed_intent,
                time_sensitivity=time_sensitivity,
                core_operation=core_operation,
                core_subject=core_subject,
                subject_modifier=subject_modifier,
                action_modifier=action_modifier,
            )
            if cache_key:
                self._cache[cache_key] = result
                if len(self._cache) > self._max_cache_size:
                    self._cache.popitem(last=False)
            return result
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning(
                f"Intent classifier timed out after {self.timeout_seconds}s for service {self.service_name}. Degrading to miss."
            )
            if cache_key:
                self._cache[cache_key] = None
                if len(self._cache) > self._max_cache_size:
                    self._cache.popitem(last=False)
            return None
        except Exception as e:
            logger.warning(
                f"Error classifying intent with service {self.service_name}: {e}. Degrading to miss.",
                exc_info=True,
            )
            if cache_key:
                self._cache[cache_key] = None
                if len(self._cache) > self._max_cache_size:
                    self._cache.popitem(last=False)
            return None

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
