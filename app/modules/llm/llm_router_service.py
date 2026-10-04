import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from fastapi import HTTPException
from qdrant_client.http import models
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import config
from app.core.constants import (
    CACHE_KEY_VERSION,
    CACHE_NEGATIVE_TTL_OFFSET,
    CALLER_SERVICE_NAME,
    EMBEDDING_DOCUMENT_PREFIX,
    EMBEDDING_QUERY_PREFIX,
    EMBEDDING_VERSION,
    INTENT_PROMPT_VERSION,
    PROMPT_VERSION,
    SCOPE_HIERARCHY,
)
from app.core.logger import log_latency
from app.modules.chat.chat_schema import ChatCompletionRequest, ChatCompletionResponse
from app.modules.embedding.embedding_service import EmbeddingService
from app.modules.gateway_requests.gateway_requests_schema import (
    GatewayRequestLogCreate,
    RoutingDecision,
)
from app.modules.gateway_requests.gateway_requests_service import GatewayRequestsService
from app.modules.intent_classifier.entity_extractor_service import (
    EntityExtractorService,
)
from app.modules.intent_classifier.intent_classifier_service import (
    IntentClassifierService,
)
from app.modules.intent_classifier.intent_schema import IntentEnum
from app.modules.llm.cache_confidence import CacheConfidenceEvaluator
from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.llm_quota.llm_quota_service import LlmQuotaService
from app.modules.llm_usage.llm_usage_schema import LlmUsageLogCreate
from app.modules.llm_usage.llm_usage_service import LlmUsageService
from app.modules.qdrant.qdrant_schema import CacheScope
from app.modules.qdrant.qdrant_service import QdrantService
from app.modules.redis.redis_service import RedisService
from app.modules.reranking.reranker_service import RerankerService

logger = logging.getLogger(__name__)


class LlmRouterService:
    def __init__(
        self,
        llm_provider_service: LlmProviderService,
        embedding_service: EmbeddingService,
        qdrant_service: QdrantService,
        redis_service: RedisService,
        reranker_service: RerankerService,
        intent_classifier_service: IntentClassifierService,
        entity_extractor_service: EntityExtractorService,
        llm_usage_service: LlmUsageService,
        llm_quota_service: LlmQuotaService = None,
        gateway_requests_service: GatewayRequestsService = None,
    ) -> None:
        self.llm_provider_service = llm_provider_service
        self.embedding_service = embedding_service
        self.qdrant_service = qdrant_service
        self.redis_service = redis_service
        self.reranker_service = reranker_service
        self.intent_classifier_service = intent_classifier_service
        self.entity_extractor_service = entity_extractor_service
        self.llm_usage_service = llm_usage_service
        self.llm_quota_service = llm_quota_service
        self.gateway_requests_service = gateway_requests_service

    async def _call_provider_with_retry(
        self,
        request: ChatCompletionRequest,
        service_name: str,
        max_retries: int | None = None,
        timeout_seconds: float | None = None,
        fallback_service: str | None = None,
        fallback_model: str | None = None,
    ) -> ChatCompletionResponse | None:
        max_retries = max_retries if max_retries is not None else config.LLM_MAX_RETRIES
        timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else config.LLM_PROVIDER_TIMEOUT_SECONDS
        )
        fallback_service = (
            fallback_service
            if fallback_service is not None
            else config.FALLBACK_LLM_SERVICE
        )
        fallback_model = (
            fallback_model if fallback_model is not None else config.FALLBACK_LLM_MODEL
        )
        fallback = config.USE_FALLBACK_LLM

        def is_retryable_exception(exc: BaseException) -> bool:
            if isinstance(exc, asyncio.TimeoutError):
                return True
            if isinstance(exc, HTTPException):
                if exc.status_code in (400, 401, 403, 500, 502, 503, 504):
                    return False
                return True
            return True

        async def attempt_call(svc: str, req: ChatCompletionRequest):
            llm_req = req.model_copy(deep=True)
            for key in ["user_id", "tenant_id", "session_id", "conversation_id"]:
                if hasattr(llm_req, key):
                    delattr(llm_req, key)

            if svc in ["gemini", "google-genai"]:
                return await self.llm_provider_service.generate_gemini_completion(
                    llm_req
                )
            elif svc == "openai":
                return await self.llm_provider_service.generate_openai_completion(
                    llm_req
                )
            elif svc == "groq":
                return await self.llm_provider_service.generate_groq_completion(llm_req)
            elif svc == "openrouter":
                return await self.llm_provider_service.generate_openrouter_completion(
                    llm_req
                )
            elif svc == "ollama":
                return await self.llm_provider_service.generate_ollama_completion(
                    llm_req
                )
            else:
                logger.warning(f"Unknown service {svc}. Returning mock response.")
                return self.llm_provider_service.generate_mock_response(llm_req)

        retryer = AsyncRetrying(
            stop=stop_after_attempt(max_retries + 1)
            if max_retries > 0
            else stop_after_attempt(1),
            wait=wait_exponential_jitter(
                initial=config.RETRY_BACKOFF_INITIAL_SECONDS,
                max=config.RETRY_BACKOFF_MAX_SECONDS,
            ),
            retry=retry_if_exception(is_retryable_exception),
            reraise=True,
        )

        try:
            async for attempt in retryer:
                with attempt:
                    return await asyncio.wait_for(
                        attempt_call(service_name, request), timeout=timeout_seconds
                    )
            return None  # Should not be reached
        except Exception as e:
            if isinstance(e, HTTPException) and e.status_code in (400, 401, 403, 500):
                logger.error(
                    f"Fatal error {e.status_code} from {service_name}. Failing immediately."
                )
                raise e

            if fallback == "true" and fallback_service:
                logger.warning(
                    f"Primary service {service_name} failed. Attempting fallback {fallback_service}."
                )
                fallback_req = request.model_copy(deep=True)
                if fallback_model:
                    fallback_req.model = fallback_model

                try:
                    return await asyncio.wait_for(
                        attempt_call(fallback_service, fallback_req),
                        timeout=timeout_seconds,
                    )
                except Exception as fallback_e:
                    logger.error(
                        f"Fallback service {fallback_service} failed: {fallback_e}"
                    )
                    raise HTTPException(status_code=503, detail="Service Unavailable")
            else:
                logger.error(
                    f"Primary service {service_name} failed and no fallback configured: {e}"
                )
                if isinstance(e, HTTPException):
                    raise e
                raise HTTPException(status_code=503, detail="Service Unavailable")

    def _generate_cache_key(self, request: ChatCompletionRequest) -> str:
        key_dict = {
            "service_name": request.service_name,
            "model": request.model,
            "temperature": request.temperature,
            "user_id": request.user_id,
            "tenant_id": request.tenant_id,
            "session_id": request.session_id,
            "conversation_id": request.conversation_id,
            "response_format": request.response_format,
            "max_tokens": request.max_tokens,
            "stop": request.stop,
            "top_p": request.top_p,
            "presence_penalty": request.presence_penalty,
            "frequency_penalty": request.frequency_penalty,
            "logit_bias": request.logit_bias,
            "messages": [
                {"role": msg.role, "content": msg.content} for msg in request.messages
            ],
        }
        key_str = json.dumps(key_dict, sort_keys=True)
        return hashlib.sha256(key_str.encode()).hexdigest()

    async def _get_embeddings(
        self, text: str, get_sparse: bool = True, prefix: str = ""
    ):
        if not text:
            return None, None
        vec = await self.embedding_service.get_embedding_async(f"{prefix}{text}")
        if get_sparse:
            sparse = self.embedding_service.get_sparse_embedding(text)
        else:
            sparse = None
        return vec, sparse

    def _prepare_prompts(self, request: ChatCompletionRequest) -> tuple[str, str, str]:
        prompt_text = "|\n".join(
            [f"{msg.role}: {msg.content}" for msg in request.messages]
        )
        prompt_text = f"model:{request.model}|{prompt_text}"

        system_messages = [
            msg.content for msg in request.messages if msg.role == "system"
        ]
        system_prompt = "\n".join(system_messages) if system_messages else ""

        user_messages = [msg.content for msg in request.messages if msg.role == "user"]
        user_prompt = "\n".join(user_messages) if user_messages else ""

        return prompt_text, system_prompt, user_prompt

    async def get_exact_match(
        self, request: ChatCompletionRequest
    ) -> tuple[dict | None, str | None]:
        exact_hash = self._generate_cache_key(request)
        tenant_id = request.tenant_id
        if self.qdrant_service:
            try:
                return self.qdrant_service.search_exact(
                    exact_hash=exact_hash, tenant_id=tenant_id
                )
            except Exception:
                logger.exception("Qdrant exact search error")
        return None, None

    async def evaluate_semantic_cache(self, request: ChatCompletionRequest) -> dict:
        prompt_text, system_prompt, user_prompt = self._prepare_prompts(request)
        logger.info(f"User prompt: {user_prompt}")

        exact_hash = self._generate_cache_key(request)
        tenant_id = request.tenant_id
        user_id = request.user_id
        session_id = request.session_id
        conversation_id = request.conversation_id
        service_name = request.service_name or config.DEFAULT_LLM_SERVICE

        # Set embedding model name based on the embedding service
        embedding_model_name = (
            self.embedding_service.model_name if self.embedding_service else "unknown"
        )
        # Set embedding version; this can be updated based on changes to embedding model versioning strategy
        embedding_version = EMBEDDING_VERSION
        # Set cache key version; this can be updated based on changes to cache key generation strategy
        cache_key_version = CACHE_KEY_VERSION
        now = int(time.time())

        result: dict[str, Any] = {
            "response": None,
            "cache_info": None,
            "miss_cache_info": None,
            "intent": None,
            "time_sensitivity": 0.0,
            "matches": [],
            "eligible_matches": [],
        }

        if config.USE_QDRANT_SEMANTIC_MATCHING != "true":
            return result

        if not self.qdrant_service:
            return result

        try:
            intent_result = await self.intent_classifier_service.classify_intent(
                request
            )
            intent = intent_result.intent
            time_sensitivity = intent_result.time_sensitivity
            core_operation = intent_result.core_operation
            core_subject = intent_result.core_subject
            subject_modifier = intent_result.subject_modifier
            action_modifier = intent_result.action_modifier
            result["intent"] = intent
            result["time_sensitivity"] = time_sensitivity
            result["core_operation"] = core_operation
            result["core_subject"] = core_subject
            result["subject_modifier"] = subject_modifier
            result["action_modifier"] = action_modifier

            logger.info(
                f"""Classified intent for prompt {exact_hash}: {intent.value} (Time Sensitivity: {time_sensitivity})
                (Core Operation: {core_operation}) (Core Subject: {core_subject})
                (Subject Modifier: {subject_modifier}) (Action Modifier: {action_modifier})"""
            )

            prompt_vector, _ = await self._get_embeddings(
                prompt_text, False, EMBEDDING_QUERY_PREFIX
            )
            system_vector, system_sparse = await self._get_embeddings(
                system_prompt, EMBEDDING_QUERY_PREFIX
            )
            user_vector, user_sparse = await self._get_embeddings(
                user_prompt, EMBEDDING_QUERY_PREFIX
            )

            must_conditions = [
                models.FieldCondition(
                    key="prompt_version", match=models.MatchValue(value="1.0")
                ),
                models.FieldCondition(
                    key="model", match=models.MatchValue(value=request.model)
                ),
                models.FieldCondition(
                    key="embedding_model",
                    match=models.MatchValue(value=embedding_model_name),
                ),
                models.FieldCondition(
                    key="embedding_version",
                    match=models.MatchValue(value=embedding_version),
                ),
                models.FieldCondition(
                    key="cacheable", match=models.MatchValue(value=True)
                ),
                models.FieldCondition(
                    key="cache_key_version",
                    match=models.MatchValue(value=cache_key_version),
                ),
                models.FieldCondition(key="expires_at", range=models.Range(gte=now)),
            ]

            if request.response_format:
                rf_str = json.dumps(request.response_format, sort_keys=True)
                rf_hash = hashlib.sha256(rf_str.encode()).hexdigest()
                must_conditions.append(
                    models.FieldCondition(
                        key="response_format_hash",
                        match=models.MatchValue(value=rf_hash),
                    )
                )
            else:
                must_conditions.append(
                    models.IsEmptyCondition(
                        is_empty=models.PayloadField(key="response_format_hash")
                    )
                )

            if request.stop:
                stop_str = json.dumps(request.stop, sort_keys=True)
                stop_hash = hashlib.sha256(stop_str.encode()).hexdigest()
                must_conditions.append(
                    models.FieldCondition(
                        key="stop_hash",
                        match=models.MatchValue(value=stop_hash),
                    )
                )
            else:
                must_conditions.append(
                    models.IsEmptyCondition(
                        is_empty=models.PayloadField(key="stop_hash")
                    )
                )

            if request.max_tokens is not None:
                must_conditions.append(
                    models.FieldCondition(
                        key="completion_tokens",
                        range=models.Range(lte=request.max_tokens),
                    )
                )

            should_conditions = [
                models.FieldCondition(
                    key="scope",
                    match=models.MatchValue(value=CacheScope.GLOBAL.value),
                )
            ]

            if tenant_id:
                should_conditions.append(
                    models.Filter(
                        must=[
                            models.FieldCondition(
                                key="scope",
                                match=models.MatchValue(value=CacheScope.TENANT.value),
                            ),
                            models.FieldCondition(
                                key="tenant_id",
                                match=models.MatchValue(value=tenant_id),
                            ),
                        ]
                    )
                )
            if user_id:
                should_conditions.append(
                    models.Filter(
                        must=[
                            models.FieldCondition(
                                key="scope",
                                match=models.MatchValue(value=CacheScope.USER.value),
                            ),
                            models.FieldCondition(
                                key="user_id",
                                match=models.MatchValue(value=user_id),
                            ),
                        ]
                    )
                )
            if session_id:
                should_conditions.append(
                    models.Filter(
                        must=[
                            models.FieldCondition(
                                key="scope",
                                match=models.MatchValue(value=CacheScope.SESSION.value),
                            ),
                            models.FieldCondition(
                                key="session_id",
                                match=models.MatchValue(value=session_id),
                            ),
                        ]
                    )
                )
            if conversation_id:
                should_conditions.append(
                    models.Filter(
                        must=[
                            models.FieldCondition(
                                key="scope",
                                match=models.MatchValue(
                                    value=CacheScope.CONVERSATION.value
                                ),
                            ),
                            models.FieldCondition(
                                key="conversation_id",
                                match=models.MatchValue(value=conversation_id),
                            ),
                        ]
                    )
                )

            query_filter = models.Filter(
                must=must_conditions,
                should=should_conditions,
            )

            # Lowered threshold to retrieve a wider pool of candidates for the Reranker
            threshold = config.QDRANT_SEARCH_THRESHOLD

            if prompt_vector:
                dense_matches = self.qdrant_service.query_points_dense(
                    vector=prompt_vector,
                    using="prompt_embedding",
                    query_filter=query_filter,
                    limit=1,
                )
                if dense_matches:
                    logger.info(
                        f"Found semantic dense matches for prompt {exact_hash}: {len(dense_matches)}"
                    )
                    for match in dense_matches:
                        logger.info(
                            f"Semantic match ID: {match['id']}, Score: {match['score']}, Prompt: {match['payload'].get('prompt', '')}"
                        )
                    top_dense_match = dense_matches[0]
                    if top_dense_match["score"] >= config.QDRANT_SEARCH_HIGH_THRESHOLD:
                        logger.info(
                            f"Fast path bypass triggered with dense score {top_dense_match['score']}"
                        )
                        matched_score = top_dense_match["score"]
                        top_prompt = top_dense_match["payload"].get("prompt", "")

                        cache_info = {
                            "matched": True,
                            "score": matched_score,
                            "rerank_score": matched_score,
                            "source": "qdrant_fast_path",
                            "cache_hit": True,
                            "point_id": top_dense_match["id"],
                            "intent": intent.value,
                            "query": prompt_text,
                            "top_1_score": matched_score,
                            "top_1_rerank_score": matched_score,
                            "top_1_prompt": top_prompt,
                            "accepted": True,
                        }

                        if "response" in top_dense_match["payload"]:
                            response = ChatCompletionResponse(
                                **top_dense_match["payload"]["response"]
                            )
                            response.cache_info = cache_info
                            result["response"] = response
                            result["cache_info"] = cache_info
                        return result

            matches = self.qdrant_service.query_points_rrf(
                system_prompt_vector=system_vector,
                system_prompt_sparse=system_sparse,
                user_prompt_vector=user_vector,
                user_prompt_sparse=user_sparse,
                filter_payload=None,
                query_filter=query_filter,
                limit=config.QDRANT_SEARCH_LIMIT,
            )

            if matches:
                logger.info(
                    f"Found semantic rrf matches for prompt {exact_hash}: {len(matches)}"
                )
                result["matches"] = matches
                for match in matches:
                    logger.info(
                        f"Semantic match ID: {match['id']}, Score: {match['score']}, Prompt: {match['payload'].get('prompt', '')}"
                    )
                eligible_matches = [
                    match for match in matches if match["score"] >= threshold
                ]
                result["eligible_matches"] = eligible_matches

                if eligible_matches:
                    request_entities = (
                        self.entity_extractor_service.get_qdrant_entity_tags(
                            prompt_text
                        )
                    )

                    matches_reranked = self.reranker_service.rerank(
                        query=prompt_text,
                        candidates=eligible_matches,
                        request_model=request.model,
                        request_service=service_name,
                        request_intent=intent.value,
                        request_temperature=request.temperature,
                        request_entities=request_entities,
                        request_core_operation=core_operation,
                        request_core_subject=core_subject,
                        request_subject_modifier=subject_modifier,
                        request_action_modifier=action_modifier,
                        user_query=user_prompt,
                        system_query=system_prompt,
                    )

                    top_match = matches_reranked[0]
                    matched_score = top_match["score"]
                    rerank_score = top_match.get("rerank_score", 0.0)

                    logger.info(
                        f"Reranked top match: {top_match['id']} with final rank score {rerank_score}"
                    )

                    # Semantic constraints
                    top_prompt = top_match["payload"].get("prompt", "")

                    # Evaluate if the cache should be accepted based on the dynamic confidence formula
                    is_accepted = CacheConfidenceEvaluator.evaluate(
                        rerank_score=rerank_score,
                        request_temperature=request.temperature,
                        request_intent=intent.value,
                    )
                    logger.info(f"Cache confidence evaluation: {is_accepted}")

                    base_cache_info = {
                        "query": prompt_text,
                        "top_1_score": matched_score,
                        "top_1_rerank_score": rerank_score,
                        "top_1_prompt": top_prompt,
                        "accepted": is_accepted,
                    }
                    if len(matches_reranked) > 1:
                        base_cache_info["top_2_score"] = matches_reranked[1]["score"]
                        base_cache_info["top_2_rerank_score"] = matches_reranked[1].get(
                            "rerank_score", 0.0
                        )
                        base_cache_info["top_2_prompt"] = matches_reranked[1][
                            "payload"
                        ].get("prompt", "")
                    if len(matches_reranked) > 2:
                        base_cache_info["top_3_score"] = matches_reranked[2]["score"]
                        base_cache_info["top_3_rerank_score"] = matches_reranked[2].get(
                            "rerank_score", 0.0
                        )
                        base_cache_info["top_3_prompt"] = matches_reranked[2][
                            "payload"
                        ].get("prompt", "")

                    if is_accepted and "response" in top_match["payload"]:
                        logger.info(
                            f"Serving response from Qdrant semantic search query with threshold {threshold}, score {matched_score}, rerank_score {rerank_score}."
                        )
                        response = ChatCompletionResponse(
                            **top_match["payload"]["response"]
                        )

                        cache_info = {
                            "matched": True,
                            "score": matched_score,
                            "rerank_score": rerank_score,
                            "source": "qdrant_semantic_path",
                            "cache_hit": is_accepted,
                            "point_id": top_match["id"],
                            "intent": intent.value,
                            **base_cache_info,
                        }
                        response.cache_info = cache_info

                        # Cache semantic match (even without a hit, to avoid future full re-reranks)
                        await self._save_to_redis_cache(
                            exact_hash,
                            response,
                            "Saving Qdrant semantic search query matched response in Redis",
                        )

                        result["response"] = response
                        result["cache_info"] = cache_info
                    else:
                        result["miss_cache_info"] = base_cache_info

                return result
            return result
        except Exception:
            logger.exception("Qdrant Database read error")
            return result

    async def save_to_cache(
        self,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse,
        intent: IntentEnum = None,
        time_sensitivity: float = 0.0,
        core_operation: str | None = None,
        core_subject: str | None = None,
        subject_modifier: str | None = None,
        action_modifier: str | None = None,
    ) -> str | None:
        if config.USE_QDRANT_SEMANTIC_MATCHING != "true":
            return None

        if not self.qdrant_service:
            return None

        prompt_text, system_prompt, user_prompt = self._prepare_prompts(request)
        exact_hash = self._generate_cache_key(request)
        tenant_id = request.tenant_id
        user_id = request.user_id
        session_id = request.session_id
        conversation_id = request.conversation_id
        service_name = request.service_name or config.DEFAULT_LLM_SERVICE

        embedding_model_name = (
            self.embedding_service.model_name if self.embedding_service else "unknown"
        )
        embedding_version = EMBEDDING_VERSION
        cache_key_version = CACHE_KEY_VERSION

        # Set created_at and expires_at timestamps for the cache entry
        now = int(time.time())
        created_at = now
        # Set expiration to 1 day in the future (can be dynamic later based on intent/entities)
        expires_at = now + config.DEFAULT_CACHE_TTL_SECONDS

        response_dump = response.model_dump(exclude={"cache_info"})

        try:
            logger.info("Embeddings generation started...")

            system_vec, system_sparse = await self._get_embeddings(
                system_prompt, True, EMBEDDING_DOCUMENT_PREFIX
            )
            user_vec, user_sparse = await self._get_embeddings(
                user_prompt, True, EMBEDDING_DOCUMENT_PREFIX
            )
            prompt_vec, prompt_sparse = await self._get_embeddings(
                prompt_text, True, EMBEDDING_DOCUMENT_PREFIX
            )
            logger.info("Embeddings generated successfully")

            if intent is None:
                intent_result = await self.intent_classifier_service.classify_intent(
                    request
                )
                intent = intent_result.intent
                time_sensitivity = intent_result.time_sensitivity
                core_operation = intent_result.core_operation
                core_subject = intent_result.core_subject
                subject_modifier = intent_result.subject_modifier
                action_modifier = intent_result.action_modifier
                logger.info(
                    f"""Classified intent for prompt {exact_hash}: {intent.value} (Time Sensitivity: {time_sensitivity})
                    (Core Operation: {core_operation}) (Core Subject: {core_subject})
                    (Subject Modifier: {subject_modifier}) (Action Modifier: {action_modifier})"""
                )

            max_scope_enum = self.entity_extractor_service.determine_max_scope(
                prompt_text
            )
            max_scope = max_scope_enum.value

            actual_scope = CacheScope.GLOBAL.value
            if request.conversation_id:
                actual_scope = CacheScope.CONVERSATION.value
            elif request.session_id:
                actual_scope = CacheScope.SESSION.value
            elif request.user_id:
                actual_scope = CacheScope.USER.value
            elif request.tenant_id:
                actual_scope = CacheScope.TENANT.value

            if SCOPE_HIERARCHY[actual_scope] > SCOPE_HIERARCHY[max_scope]:
                actual_scope = max_scope

            entities = self.entity_extractor_service.get_qdrant_entity_tags(prompt_text)

            ttl = self.intent_classifier_service.calculate_ttl(intent, time_sensitivity)
            if ttl is not None:
                expires_at = now + ttl
            else:
                expires_at = now + CACHE_NEGATIVE_TTL_OFFSET

            metadata = {
                "prompt_version": PROMPT_VERSION,
                "intent_prompt_version": INTENT_PROMPT_VERSION,
                "temperature": request.temperature,
                "subject_modifier": subject_modifier,
                "action_modifier": action_modifier,
            }
            if request.response_format:
                rf_str = json.dumps(request.response_format, sort_keys=True)
                metadata["response_format_hash"] = hashlib.sha256(
                    rf_str.encode()
                ).hexdigest()

            if request.stop:
                stop_str = json.dumps(request.stop, sort_keys=True)
                metadata["stop_hash"] = hashlib.sha256(stop_str.encode()).hexdigest()

            if response.usage and response.usage.completion_tokens:
                metadata["completion_tokens"] = response.usage.completion_tokens

            logger.info("Saving generated embedding in Qdrant...")

            point_id = self.qdrant_service.upsert(
                prompt=prompt_text,
                response=response_dump,
                system_prompt_vector=system_vec,
                system_prompt_sparse=system_sparse,
                user_prompt_vector=user_vec,
                user_prompt_sparse=user_sparse,
                prompt_vector=prompt_vec,
                prompt_sparse=prompt_sparse,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                exact_hash=exact_hash,
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                conversation_id=conversation_id,
                model=request.model,
                service_name=service_name,
                scope=actual_scope,
                entities=entities,
                time_sensitivity=time_sensitivity,
                intent=intent.value,
                core_operation=core_operation,
                core_subject=core_subject,
                embedding_model=embedding_model_name,
                embedding_version=embedding_version,
                cacheable=True,
                cache_key_version=cache_key_version,
                created_at=created_at,
                expires_at=expires_at,
                metadata=metadata,
            )
            if point_id is not None:
                logger.info(
                    f"Generated embedding saved successfully in Qdrant for {exact_hash}"
                )
            return point_id
        except Exception:
            logger.exception("Embedding creation error")
            return None

    async def _get_redis_exact_match(
        self, exact_hash: str, service_name: str
    ) -> ChatCompletionResponse | None:
        if config.USE_REDIS_SEMANTIC_MATCHING != "true":
            return None

        if not self.redis_service:
            return None

        cached_data = await self.redis_service.get(exact_hash)
        if cached_data:
            logger.info(
                f"Serving response from Redis Cache for {exact_hash} using {service_name}"
            )
            response = ChatCompletionResponse(**json.loads(cached_data))
            response.cache_info = {
                "matched": True,
                "score": 1.0,
                "source": "redis",
                "cache_hit": True,
                "point_id": None,
            }
            return response
        return None

    async def _get_qdrant_exact_match(
        self, exact_hash: str, tenant_id: str | None, prompt_text: str
    ) -> tuple[ChatCompletionResponse | None, str | None]:
        if config.USE_QDRANT_SEMANTIC_MATCHING != "true":
            return None, None

        if not self.qdrant_service:
            return None, None

        cached_payload, point_id = self.qdrant_service.search_exact(
            exact_hash=exact_hash, tenant_id=tenant_id
        )
        if cached_payload and "response" in cached_payload:
            logger.info(
                f"Serving response from Qdrant exact search query for {exact_hash}"
            )
            response = ChatCompletionResponse(**cached_payload["response"])
            intent = cached_payload.get("intent", IntentEnum.EMPTY.value)
            response.cache_info = {
                "matched": True,
                "score": 1.0,
                "rerank_score": 0.0,
                "source": "qdrant_exact_path",
                "cache_hit": True,
                "point_id": point_id,
                "intent": intent,
                "query": prompt_text,
                "accepted": True,
            }
            return response, point_id
        return None, None

    async def _save_to_redis_cache(
        self, exact_hash: str, response: ChatCompletionResponse, log_message: str
    ) -> None:
        if config.USE_REDIS_SEMANTIC_MATCHING != "true":
            return

        if self.redis_service:
            logger.info(log_message)
            response_dump = response.model_dump(exclude={"cache_info"})
            await self.redis_service.set(exact_hash, json.dumps(response_dump))

    async def _log_usage(
        self,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse,
        service_name: str,
        start_time: float,
        exact_hash: str,
        api_key: str | None = None,
    ) -> None:
        try:
            end_time = time.perf_counter()
            latency_ms = int((end_time - start_time) * 1000)
            is_cache_hit = (
                response.cache_info.get("cache_hit", False)
                if hasattr(response, "cache_info")
                and isinstance(response.cache_info, dict)
                else False
            )

            prompt_text, _, _ = self._prepare_prompts(request)
            input_text = prompt_text
            output_text = (
                response.choices[0].message.content if response.choices else ""
            )
            prompt_tokens = (
                response.usage.prompt_tokens
                if response and response.usage and response.usage.prompt_tokens
                else 0
            )
            completion_tokens = (
                response.usage.completion_tokens
                if response and response.usage and response.usage.completion_tokens
                else 0
            )
            total_tokens = (
                response.usage.total_tokens
                if response and response.usage and response.usage.total_tokens
                else 0
            )

            cache_read_tokens = total_tokens if is_cache_hit else 0
            cache_creation_tokens = 0 if is_cache_hit else total_tokens

            finish_reason = None
            if response.choices and len(response.choices) > 0:
                finish_reason = response.choices[0].finish_reason

            log_data = LlmUsageLogCreate(
                service_name=service_name,
                model=request.model,
                caller_service_name=CALLER_SERVICE_NAME,
                input_text=input_text,
                output_text=output_text,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
                cost=0.0,
                latency_ms=latency_ms,
                status_code=200,
                is_success=True,
                finish_reason=finish_reason,
            )

            self.llm_usage_service.create_llm_usage_log(log_data)
            logger.info(f"LLM usage logged successfully for {exact_hash}")

            # Record tokens for TPM limit
            if self.llm_quota_service and api_key and total_tokens > 0:
                try:
                    await self.llm_quota_service.record_tokens(
                        api_key=api_key,
                        total_tokens=total_tokens,
                        provider=service_name,
                        model=request.model,
                        user_id=request.user_id,
                    )
                except Exception:
                    logger.exception("Failed to record TPM usage")
        except Exception:
            logger.exception("Failed to log LLM usage")

    def _log_gateway_request(
        self,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse,
        service_name: str,
        start_time: float,
        exact_hash: str,
    ) -> None:
        if not self.gateway_requests_service:
            return

        try:
            end_time = time.perf_counter()
            latency_ms = int((end_time - start_time) * 1000)
            prompt_text, _, _ = self._prepare_prompts(request)

            source = (
                response.cache_info.get("source", "llm")
                if hasattr(response, "cache_info")
                else "llm"
            )
            routing_decision = RoutingDecision.LLM
            if source == "redis":
                routing_decision = RoutingDecision.REDIS_EXACT
            elif source == "qdrant_exact_path":
                routing_decision = RoutingDecision.QDRANT_EXACT
            elif source == "qdrant_semantic_path":
                routing_decision = RoutingDecision.QDRANT_SEMANTIC
            elif source == "qdrant_fast_path":
                routing_decision = RoutingDecision.QDRANT_FAST

            cache_info = response.cache_info if hasattr(response, "cache_info") else {}

            output_text = (
                response.choices[0].message.content if response.choices else ""
            )

            log_data = GatewayRequestLogCreate(
                query_text=prompt_text,
                response_text=output_text,
                routing_decision=routing_decision,
                provider=service_name if source == "llm" else "cache",
                model=request.model,
                point_id=cache_info.get("point_id"),
                exact_hash=exact_hash,
                intent=cache_info.get("intent"),
                core_operation=cache_info.get("core_operation"),
                core_subject=cache_info.get("core_subject"),
                subject_modifier=cache_info.get("subject_modifier"),
                action_modifier=cache_info.get("action_modifier"),
                rerank_score=cache_info.get("rerank_score")
                if source == "semantic"
                else None,
                latency_ms=latency_ms,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                conversation_id=request.conversation_id,
            )
            self.gateway_requests_service.log_request(log_data)
        except Exception:
            logger.exception("Failed to log gateway request")

    @log_latency()
    async def generate_completion(
        self, request: ChatCompletionRequest, api_key: str | None = None
    ) -> ChatCompletionResponse:
        start_time = time.perf_counter()
        service_name = request.service_name or config.DEFAULT_LLM_SERVICE
        logger.info(f"Generating completion for service {service_name}")

        prompt_text, _, _ = self._prepare_prompts(request)
        exact_hash = self._generate_cache_key(request)
        tenant_id = request.tenant_id

        response: ChatCompletionResponse | None = None

        try:
            # 1. Try exact match from Redis first
            response = await self._get_redis_exact_match(exact_hash, service_name)
            if response:
                return response

            # 2. Try Exact Match in Qdrant
            response, _ = await self._get_qdrant_exact_match(
                exact_hash, tenant_id, prompt_text
            )
            if response:
                await self._save_to_redis_cache(
                    exact_hash,
                    response,
                    "Saving Qdrant exact search query matched response in Redis",
                )
                return response

            # 3. Try Semantic Match if Exact misses
            semantic_result = await self.evaluate_semantic_cache(request)
            if semantic_result.get("response"):
                response = semantic_result["response"]
                return response

            # Extract intent and other metadata if intent classification has been called already
            intent = semantic_result.get("intent", None)
            time_sensitivity = semantic_result.get("time_sensitivity", 0.0)
            core_operation = semantic_result.get("core_operation", None)
            core_subject = semantic_result.get("core_subject", None)
            subject_modifier = semantic_result.get("subject_modifier", None)
            action_modifier = semantic_result.get("action_modifier", None)
            miss_cache_info = semantic_result.get("miss_cache_info")

            response = await self._call_provider_with_retry(
                request=request,
                service_name=service_name,
                max_retries=config.LLM_MAX_RETRIES,
                timeout_seconds=config.LLM_PROVIDER_TIMEOUT_SECONDS,
                fallback_service=config.FALLBACK_LLM_SERVICE,
                fallback_model=config.FALLBACK_LLM_MODEL,
            )

            if response and response.choices:
                point_id = await self.save_to_cache(
                    request=request,
                    response=response,
                    intent=intent,
                    time_sensitivity=time_sensitivity,
                    core_operation=core_operation,
                    core_subject=core_subject,
                    subject_modifier=subject_modifier,
                    action_modifier=action_modifier,
                )

                cache_info = {
                    "matched": False,
                    "score": 0.0,
                    "source": "llm",
                    "cache_hit": False,
                    "point_id": point_id,
                    "intent": intent.value if intent else IntentEnum.EMPTY.value,
                    "query": prompt_text,
                    "accepted": False,
                    "core_operation": core_operation,
                    "core_subject": core_subject,
                    "subject_modifier": subject_modifier,
                    "action_modifier": action_modifier,
                }
                if miss_cache_info:
                    cache_info.update(miss_cache_info)

                response.cache_info = cache_info

                await self._save_to_redis_cache(
                    exact_hash,
                    response,
                    "Saving LLM generated response in Redis",
                )

            return response
        finally:
            if response:
                await self._log_usage(
                    request=request,
                    response=response,
                    service_name=service_name,
                    start_time=start_time,
                    exact_hash=exact_hash,
                    api_key=api_key,
                )
                self._log_gateway_request(
                    request=request,
                    response=response,
                    service_name=service_name,
                    start_time=start_time,
                    exact_hash=exact_hash,
                )
