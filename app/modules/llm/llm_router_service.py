import asyncio
import hashlib
import json
import logging
import time

from fastapi import HTTPException
from qdrant_client.http import models
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import (
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_LLM_SERVICE,
    FALLBACK_LLM_MODEL,
    FALLBACK_LLM_SERVICE,
    LLM_MAX_RETRIES,
    LLM_PROVIDER_TIMEOUT_SECONDS,
    QDRANT_SEARCH_LIMIT,
    QDRANT_SEARCH_THRESHOLD,
    RETRY_BACKOFF_INITIAL_SECONDS,
    RETRY_BACKOFF_MAX_SECONDS,
)
from app.core.constants import (
    CACHE_KEY_VERSION,
    CACHE_NEGATIVE_TTL_OFFSET,
    CALLER_SERVICE_NAME,
    EMBEDDING_DOCUMENT_PREFIX,
    EMBEDDING_QUERY_PREFIX,
    EMBEDDING_VERSION,
    PROMPT_VERSION,
    SCOPE_HIERARCHY,
)
from app.modules.chat.chat_schema import ChatCompletionRequest, ChatCompletionResponse
from app.modules.embedding.embedding_service import EmbeddingService
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

    async def _call_provider_with_retry(
        self,
        request: ChatCompletionRequest,
        service_name: str,
        max_retries: int = LLM_MAX_RETRIES,
        timeout_seconds: float = LLM_PROVIDER_TIMEOUT_SECONDS,
        fallback_service: str | None = FALLBACK_LLM_SERVICE,
        fallback_model: str | None = FALLBACK_LLM_MODEL,
    ) -> ChatCompletionResponse | None:
        def is_retryable_exception(exc: BaseException) -> bool:
            if isinstance(exc, asyncio.TimeoutError):
                return True
            if isinstance(exc, HTTPException):
                if exc.status_code in (400, 401, 403, 500, 502, 503, 504):
                    return False
                return True
            return True

        async def attempt_call(svc: str, req: ChatCompletionRequest):
            llm_req = req.copy(deep=True)
            for key in ["user_id", "tenant_id", "session_id", "conversation_id"]:
                if hasattr(llm_req, key):
                    delattr(llm_req, key)

            if svc in ["gemini", "google-genai", "google genai"]:
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
                initial=RETRY_BACKOFF_INITIAL_SECONDS, max=RETRY_BACKOFF_MAX_SECONDS
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

            if fallback_service:
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
            "messages": [
                {"role": msg.role, "content": msg.content} for msg in request.messages
            ],
        }
        key_str = json.dumps(key_dict, sort_keys=True)
        return hashlib.sha256(key_str.encode()).hexdigest()

    async def _get_embeddings(self, text: str, prefix: str = EMBEDDING_QUERY_PREFIX):
        if not text:
            return None, None
        vec = await self.embedding_service.get_embedding_async(f"{prefix}{text}")
        sparse = self.embedding_service.get_sparse_embedding(text)
        return vec, sparse

    async def _get_doc_embeddings(
        self, text: str, prefix: str = EMBEDDING_DOCUMENT_PREFIX
    ):
        if not text:
            return None, None
        vec = await self.embedding_service.get_embedding_async(f"{prefix}{text}")
        sparse = self.embedding_service.get_sparse_embedding(text)
        return vec, sparse

    def _prepare_prompts(
        self, request: ChatCompletionRequest
    ) -> tuple[str, str | None, str | None]:
        prompt_text = "|\n".join(
            [f"{msg.role}: {msg.content}" for msg in request.messages]
        )
        prompt_text = f"model:{request.model}|{prompt_text}"

        system_messages = [
            msg.content for msg in request.messages if msg.role == "system"
        ]
        system_prompt = "\n".join(system_messages) if system_messages else None

        user_messages = [msg.content for msg in request.messages if msg.role == "user"]
        user_prompt = "\n".join(user_messages) if user_messages else None

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
            except Exception as e:
                logger.error(f"Qdrant exact search error: {e}")
        return None, None

    async def evaluate_semantic_cache(self, request: ChatCompletionRequest) -> dict:
        prompt_text, system_prompt, user_prompt = self._prepare_prompts(request)
        logger.info(f"User prompt: {user_prompt}")

        exact_hash = self._generate_cache_key(request)
        tenant_id = request.tenant_id
        user_id = request.user
        session_id = request.session_id
        conversation_id = request.conversation_id
        service_name = request.service_name or DEFAULT_LLM_SERVICE

        # Set embedding model name based on the embedding service
        embedding_model_name = (
            self.embedding_service.model_name if self.embedding_service else "unknown"
        )
        # Set embedding version; this can be updated based on changes to embedding model versioning strategy
        embedding_version = EMBEDDING_VERSION
        # Set cache key version; this can be updated based on changes to cache key generation strategy
        cache_key_version = CACHE_KEY_VERSION
        now = int(time.time())

        result = {
            "response": None,
            "cache_info": None,
            "miss_cache_info": None,
            "intent": None,
            "time_sensitivity": 0.0,
            "matches": [],
            "eligible_matches": [],
        }

        if not self.qdrant_service:
            return result

        try:
            intent_result = await self.intent_classifier_service.classify_intent(
                request
            )
            intent = intent_result.intent
            time_sensitivity = intent_result.time_sensitivity
            result["intent"] = intent
            result["time_sensitivity"] = time_sensitivity

            logger.info(
                f"Classified intent for prompt {exact_hash}: {intent.value} (Time Sensitivity: {time_sensitivity})"
            )

            system_vector, system_sparse = await self._get_embeddings(system_prompt)
            user_vector, user_sparse = await self._get_embeddings(user_prompt)

            must_conditions = [
                models.FieldCondition(
                    key="prompt_version", match=models.MatchValue(value="1.0")
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
                models.FieldCondition(
                    key="model", match=models.MatchValue(value=request.model)
                ),
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
            threshold = QDRANT_SEARCH_THRESHOLD

            matches = self.qdrant_service.query_points(
                system_prompt_vector=system_vector,
                system_prompt_sparse=system_sparse,
                user_prompt_vector=user_vector,
                user_prompt_sparse=user_sparse,
                filter_payload=None,
                query_filter=query_filter,
                limit=QDRANT_SEARCH_LIMIT,
            )
            result["matches"] = matches
            logger.info(
                f"Found semantic matches for prompt {exact_hash}: {len(matches)}"
            )
            for match in matches:
                logger.info(
                    f"Semantic match ID: {match['id']}, Score: {match['score']}, Prompt: {match['payload'].get('prompt', '')}"
                )
            eligible_matches = [
                match for match in matches if match["score"] >= threshold
            ]
            result["eligible_matches"] = eligible_matches

            if eligible_matches:
                request_entities = self.entity_extractor_service.get_qdrant_entity_tags(
                    prompt_text
                )

                matches_reranked = self.reranker_service.rerank(
                    query=prompt_text,
                    candidates=eligible_matches,
                    request_model=request.model,
                    request_service=service_name,
                    request_intent=intent.value,
                    request_temperature=request.temperature,
                    request_entities=request_entities,
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
                    final_score=rerank_score,
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
                        "source": "qdrant",
                        "cache_hit": is_accepted,
                        "point_id": top_match["id"],
                        "intent": intent.value,
                        **base_cache_info,
                    }
                    response.cache_info = cache_info
                    # response_dump = response.model_dump(exclude={"cache_info"})

                    # Cache semantic match (even without a hit, to avoid future full re-reranks)
                    # if self.redis_service:
                    #     logger.info(
                    #         "Saving Qdrant semantic search query matched response in Redis"
                    #     )
                    #     await self.redis_service.set(
                    #         exact_hash, json.dumps(response_dump)
                    #     )

                    result["response"] = response
                    result["cache_info"] = cache_info
                else:
                    result["miss_cache_info"] = base_cache_info

            return result
        except Exception as e:
            logger.error(f"Qdrant Database read error: {e}")
            return result

    async def save_to_cache(
        self,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse,
        intent: IntentEnum = None,
        time_sensitivity: float = 0.0,
    ) -> str | None:
        if not self.qdrant_service:
            return None

        prompt_text, system_prompt, user_prompt = self._prepare_prompts(request)
        exact_hash = self._generate_cache_key(request)
        tenant_id = request.tenant_id
        user_id = request.user
        session_id = request.session_id
        conversation_id = request.conversation_id
        service_name = request.service_name or DEFAULT_LLM_SERVICE

        embedding_model_name = (
            self.embedding_service.model_name if self.embedding_service else "unknown"
        )
        embedding_version = EMBEDDING_VERSION
        cache_key_version = CACHE_KEY_VERSION

        # Set created_at and expires_at timestamps for the cache entry
        now = int(time.time())
        created_at = now
        # Set expiration to 1 day in the future (can be dynamic later based on intent/entities)
        expires_at = now + DEFAULT_CACHE_TTL_SECONDS

        response_dump = response.model_dump(exclude={"cache_info"})

        try:
            logger.info("Embeddings generation started...")

            system_vec, system_sparse = await self._get_doc_embeddings(system_prompt)
            user_vec, user_sparse = await self._get_doc_embeddings(user_prompt)
            prompt_vec, prompt_sparse = await self._get_doc_embeddings(prompt_text)
            logger.info("Embeddings generated successfully")

            if intent is None:
                intent_result = await self.intent_classifier_service.classify_intent(
                    request
                )
                intent = intent_result.intent
                time_sensitivity = intent_result.time_sensitivity
                logger.info(
                    f"Classified intent for prompt {exact_hash}: {intent.value}"
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
            elif request.user:
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
                "temperature": request.temperature,
                "intent": intent.value,
            }
            if request.response_format:
                rf_str = json.dumps(request.response_format, sort_keys=True)
                metadata["response_format_hash"] = hashlib.sha256(
                    rf_str.encode()
                ).hexdigest()

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
        except Exception as e:
            logger.error(f"Embedding creation error: {e}")
            return None

    async def generate_completion(
        self, request: ChatCompletionRequest, api_key: str = None
    ) -> ChatCompletionResponse:
        start_time = time.time()
        service_name = request.service_name or DEFAULT_LLM_SERVICE
        logger.warning(f"Generating completion for service {service_name}")

        prompt_text, _, _ = self._prepare_prompts(request)
        exact_hash = self._generate_cache_key(request)
        tenant_id = request.tenant_id

        response = None

        # 1. Try exact match from Redis first
        # if self.redis_service:
        #     cached_data = await self.redis_service.get(exact_hash)
        #     if cached_data:
        #         logger.info(
        #             f"Serving response from Redis Cache for {exact_hash} using {service_name}"
        #         )
        #         response = ChatCompletionResponse(**json.loads(cached_data))
        #         response.cache_info = {
        #             "matched": True,
        #             "score": 1.0,
        #             "source": "redis",
        #             "cache_hit": True,
        #             "point_id": None,
        #         }

        intent = None
        miss_cache_info = None

        if not response and self.qdrant_service:
            # # 2. Try Exact Match in Qdrant
            # cached_payload, point_id = self.qdrant_service.search_exact(
            #     exact_hash=exact_hash, tenant_id=tenant_id
            # )
            # if cached_payload and "response" in cached_payload:
            #     logger.info(
            #         f"Serving response from Qdrant exact search query for {exact_hash}"
            #     )
            #     response = ChatCompletionResponse(**cached_payload["response"])
            #     intent = cached_payload.get(
            #         "intent", IntentEnum.GENERAL_QUERY.value
            #     )
            #     response.cache_info = {
            #         "matched": True,
            #         "score": 1.0,
            #         "rerank_score": 1.0,
            #         "source": "qdrant",
            #         "cache_hit": True,
            #         "point_id": point_id,
            #         "intent": intent,
            #         "query": prompt_text,
            #         "accepted": True,
            #     }
            #     response_dump = response.model_dump(exclude={"cache_info"})
            #
            #     # Cache this exact match in Redis
            #     if self.redis_service:
            #         logger.info(
            #             f"Saving Qdrant exact search query matched response in Redis for {exact_hash}"
            #         )
            #         await self.redis_service.set(
            #             exact_hash, json.dumps(response_dump)
            #         )

            # 3. Try Semantic Match if Exact misses
            if not response:
                semantic_result = await self.evaluate_semantic_cache(request)
                if semantic_result.get("response"):
                    response = semantic_result["response"]

                intent = semantic_result.get("intent")
                miss_cache_info = semantic_result.get("miss_cache_info")

        if not response:
            response = await self._call_provider_with_retry(
                request=request,
                service_name=service_name,
                max_retries=3,
                timeout_seconds=15.0,
                fallback_service="ollama",
                fallback_model="llama3.1:8b",
            )

            if response and response.choices:
                point_id = await self.save_to_cache(
                    request=request,
                    response=response,
                    intent=intent,
                    time_sensitivity=semantic_result.get("time_sensitivity", 0.0)
                    if intent
                    else 0.0,
                )

                cache_info = {
                    "matched": False,
                    "score": 0.0,
                    "source": "llm",
                    "cache_hit": False,
                    "point_id": point_id,
                    "intent": intent.value
                    if intent
                    else IntentEnum.GENERAL_QUERY.value,
                    "query": prompt_text,
                    "accepted": False,
                }
                if miss_cache_info:
                    cache_info.update(miss_cache_info)

                response.cache_info = cache_info

                # if self.redis_service:
                #     logger.info(
                #         f"Saving LLM generated response in Redis for {exact_hash}"
                #     )
                #     response_dump = response.model_dump(exclude={"cache_info"})
                #     await self.redis_service.set(
                #         exact_hash, json.dumps(response_dump)
                #     )

        if response:
            try:
                latency_ms = int((time.time() - start_time) * 1000)
                is_cache_hit = (
                    response.cache_info.get("cache_hit", False)
                    if hasattr(response, "cache_info")
                    and isinstance(response.cache_info, dict)
                    else False
                )

                input_text = prompt_text
                output_text = (
                    response.choices[0].message.content if response.choices else ""
                )
                prompt_tokens = response.usage.prompt_tokens if response.usage else 0
                completion_tokens = (
                    response.usage.completion_tokens if response.usage else 0
                )
                total_tokens = response.usage.total_tokens if response.usage else 0

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
                    user_id=request.user,
                    tenant_id=tenant_id,
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
                            user=request.user,
                        )
                    except Exception as e:
                        logger.error(f"Failed to record TPM usage: {e}")
            except Exception as e:
                logger.error(f"Failed to log LLM usage: {e}")

        return response
