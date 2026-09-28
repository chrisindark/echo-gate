import hashlib
import json
import logging
import time

from app.modules.chat.chat_schema import ChatCompletionRequest, ChatCompletionResponse
from app.modules.embedding.embedding_service import EmbeddingService
from app.modules.intent_classifier.intent_classifier_service import (
    IntentClassifierService,
)
from app.modules.intent_classifier.intent_schema import IntentEnum
from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.llm_usage.llm_usage_schema import LlmUsageLogCreate
from app.modules.llm_usage.llm_usage_service import LlmUsageService
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
        llm_usage_service: LlmUsageService,
    ) -> None:
        self.llm_provider_service = llm_provider_service
        self.embedding_service = embedding_service
        self.qdrant_service = qdrant_service
        self.redis_service = redis_service
        self.reranker_service = reranker_service
        self.intent_classifier_service = intent_classifier_service
        self.llm_usage_service = llm_usage_service

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

    async def generate_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        start_time = time.time()
        logger.info(f"Service name {request.service_name}")
        service_name = request.service_name or "ollama"
        logger.warning(f"Generating completion for service {service_name}")

        prompt_text = "\n".join(
            [f"{msg.role}: {msg.content}" for msg in request.messages]
        )
        prompt_text = f"model:{request.model}|{prompt_text}"
        exact_hash = self._generate_cache_key(request)
        tenant_id = request.user or "anonymous"

        response = None
        # Set tenant_id as the user from the request or default to "anonymous"
        tenant_id = request.user or "anonymous"
        # Set embedding model name based on the embedding service
        embedding_model_name = (
            self.embedding_service.model_name if self.embedding_service else "unknown"
        )
        # Set embedding version; this can be updated based on changes to versioning strategy
        embedding_version = "v1"
        # Set created_at and expires_at timestamps for the cache entry
        now = int(time.time())
        created_at = now
        # Set expiration to 1 day ago for filtering out old cache entries
        expires_at = now - (1 * 24 * 60 * 60)

        # 1. Try exact match from Redis first
        if self.redis_service:
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

        intent = None
        if not response and self.embedding_service and self.qdrant_service:
            cached_payload = None
            try:
                # 2. Try Exact Match in Qdrant
                cached_payload, point_id = self.qdrant_service.search_exact(
                    exact_hash=exact_hash, tenant_id=tenant_id
                )
                if cached_payload and "response" in cached_payload:
                    logger.info(
                        f"Serving response from Qdrant exact search query for {exact_hash}"
                    )
                    response = ChatCompletionResponse(**cached_payload["response"])
                    intent = cached_payload.get(
                        "intent", IntentEnum.GENERAL_QUERY.value
                    )
                    response.cache_info = {
                        "matched": True,
                        "score": 1.0,
                        "rerank_score": 1.0,
                        "source": "qdrant",
                        "cache_hit": True,
                        "point_id": point_id,
                        "intent": intent,
                        "query": prompt_text,
                        "accepted": True,
                    }
                    response_dump = response.model_dump(exclude={"cache_info"})

                    # Cache this exact match in Redis
                    if self.redis_service:
                        logger.info(
                            f"Saving Qdrant exact search query matched response in Redis for {exact_hash}"
                        )
                        await self.redis_service.set(
                            exact_hash, json.dumps(response_dump)
                        )

                # 3. Try Semantic Match if Exact misses
                if not cached_payload:
                    intent = await self.intent_classifier_service.classify_intent(
                        prompt_text
                    )
                    logger.info(
                        f"Classified intent for prompt {exact_hash}: {intent.value}"
                    )

                    vector = await self.embedding_service.get_embedding_async(
                        f"search_query: {prompt_text}"
                    )
                    filter_payload = {
                        "tenant_id": tenant_id,
                        "model": request.model,
                        "prompt_version": "1.0",
                        "embedding_model": embedding_model_name,
                        "embedding_version": embedding_version,
                        "cacheable": True,
                        "cache_key_version": "v1",
                        "intent": intent.value,
                        # "temperature": request.temperature, # float values can be tricky to filter on; consider rounding or using a range if needed
                        # "expires_at": expires_at, # filtering out expired entries; ensure your Qdrant collection has this field indexed and uses range filtering if needed
                    }
                    # Set a threshold for semantic similarity; bumped to 0.95 for strict caching
                    threshold = 0.95
                    matches = self.qdrant_service.query_points(
                        vector, filter_payload=filter_payload, limit=10
                    )
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

                    miss_cache_info = None
                    if eligible_matches:
                        matches = self.reranker_service.rerank(
                            query=prompt_text, candidates=eligible_matches
                        )

                        top_match = matches[0]
                        matched_score = top_match["score"]
                        rerank_threshold = (
                            0.0  # This can be adjusted based on your reranking strategy
                        )
                        rerank_score = top_match.get("rerank_score", 0.0)

                        # Semantic constraints
                        top_prompt = top_match["payload"].get("prompt", "")

                        # Determine if the top match is accepted based on a rerank score threshold and semantic rules
                        is_accepted = rerank_score > rerank_threshold
                        base_cache_info = {
                            "query": prompt_text,
                            "top_1_score": matched_score,
                            "top_1_rerank_score": rerank_score,
                            "top_1_prompt": top_match["payload"].get("prompt", ""),
                            "accepted": is_accepted,
                        }
                        if len(matches) > 1:
                            base_cache_info["top_2_score"] = matches[1]["score"]
                            base_cache_info["top_2_rerank_score"] = matches[1].get(
                                "rerank_score", 0.0
                            )
                            base_cache_info["top_2_prompt"] = matches[1]["payload"].get(
                                "prompt", ""
                            )
                        if len(matches) > 2:
                            base_cache_info["top_3_score"] = matches[2]["score"]
                            base_cache_info["top_3_rerank_score"] = matches[2].get(
                                "rerank_score", 0.0
                            )
                            base_cache_info["top_3_prompt"] = matches[2]["payload"].get(
                                "prompt", ""
                            )

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
                                "cache_hit": False,
                                "point_id": top_match["id"],
                                "intent": intent.value,
                                **base_cache_info,
                            }
                            response.cache_info = cache_info
                            response_dump = response.model_dump(exclude={"cache_info"})

                            if self.redis_service:
                                logger.info(
                                    "Saving Qdrant semantic search query matched response in Redis"
                                )
                                await self.redis_service.set(
                                    exact_hash, json.dumps(response_dump)
                                )
                        else:
                            miss_cache_info = base_cache_info
            except Exception as e:
                logger.error(f"Qdrant Database read error: {e}")

        if not response:
            if service_name in ["gemini", "google-genai", "google genai"]:
                response = await self.llm_provider_service.generate_gemini_completion(
                    request
                )
            elif service_name == "openai":
                response = await self.llm_provider_service.generate_openai_completion(
                    request
                )
            elif service_name == "ollama":
                response = await self.llm_provider_service.generate_ollama_completion(
                    request
                )
            else:
                logger.warning(
                    f"Unknown service {service_name}. Returning mock response."
                )
                response = self.llm_provider_service.generate_mock_response(request)

            if response and response.choices:
                try:
                    response_dump = response.model_dump(exclude={"cache_info"})
                    logger.info(
                        f"LLM provider service generated response successfully for {exact_hash}"
                    )

                    point_id = None
                    if self.embedding_service and self.qdrant_service:
                        try:
                            logger.info("Embedding generated response...")
                            vector = await self.embedding_service.get_embedding_async(
                                f"search_document: {prompt_text}"
                            )
                            logger.info("Embedding generated successfully")
                            logger.info("Saving generated embedding in Qdrant...")

                            # Classify the intent of the prompt using the IntentClassifierService if not already done
                            if intent is None:
                                intent = await self.intent_classifier_service.classify_intent(
                                    prompt_text
                                )
                                logger.info(
                                    f"Classified intent for prompt {exact_hash}: {intent.value}"
                                )

                            # Updated schema usage
                            metadata = {
                                "prompt_version": "1.0",
                                "temperature": request.temperature,
                                "intent": intent.value,
                            }

                            point_id = self.qdrant_service.upsert(
                                vector=vector,
                                prompt=prompt_text,
                                response=response_dump,
                                exact_hash=exact_hash,
                                tenant_id=tenant_id,
                                model=request.model,
                                embedding_model=embedding_model_name,
                                embedding_version=embedding_version,
                                cacheable=True,
                                cache_key_version="v1",
                                created_at=created_at,
                                expires_at=expires_at,
                                metadata=metadata,
                            )
                            if point_id is not None:
                                logger.info(
                                    f"Generated embedding saved successfully in Qdrant for {exact_hash}"
                                )
                        except Exception as e:
                            logger.error(f"Embedding creation error: {e}")

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
                    if "miss_cache_info" in locals() and miss_cache_info:
                        cache_info.update(miss_cache_info)

                    response.cache_info = cache_info
                    if self.redis_service:
                        logger.info(
                            f"Saving LLM generated response in Redis for {exact_hash}"
                        )
                        await self.redis_service.set(
                            exact_hash, json.dumps(response_dump)
                        )
                except Exception as e:
                    logger.error(f"Redis or Qdrant write error: {e}")

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
                    caller_service_name="echo-gate-api",
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
            except Exception as e:
                logger.error(f"Failed to log LLM usage: {e}")

        return response
