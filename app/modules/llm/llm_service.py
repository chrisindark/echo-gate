import hashlib
import json
import logging
import os
import time
import uuid

import httpx
from fastapi import HTTPException
from google import genai
from google.genai import types

from app.modules.chat.chat_schema import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    Usage,
)
from app.modules.embedding.embedding_service import EmbeddingService
from app.modules.qdrant.qdrant_service import QdrantService
from app.modules.redis.redis_service import RedisService
from app.modules.reranking.reranker_service import RerankerService

# from app.modules.usage.usage_service import UsageService
# from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


class LlmService:
    def __init__(
        self,
        embedding_service: EmbeddingService,
        qdrant_service: QdrantService,
        redis_service: RedisService,
        reranker_service: RerankerService,
    ) -> None:
        self.openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
        self.openai_base_url: str = "https://api.openai.com/v1/chat/completions"
        self.gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
        self.ollama_base_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")

        self.gemini_client = None
        if genai and self.gemini_api_key:
            self.gemini_client = genai.Client(api_key=self.gemini_api_key)

        self.embedding_service = embedding_service
        self.qdrant_service = qdrant_service
        self.redis_service = redis_service
        self.reranker_service = reranker_service

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

        # Consider checking request.temperature to ascertain if cache needs to be checked or a new LLM response needs to be fetched
        # if request.temperature <= 0.4:
        # 1. Check Redis (Exact)
        # 2. Check Qdrant (Semantic)
        # 3. Return cache hit if found

        # If temperature > 0.4 route directly to LLM
        # 1. Ignore Redis and Qdrant and send request to LLM

        # 1. Try exact match from Redis first
        if self.redis_service:
            cached_data = await self.redis_service.get(exact_hash)
            if cached_data:
                logger.info(
                    f"Serving response from Redis Cache for {exact_hash} using {service_name}"
                )
                response = ChatCompletionResponse(**cached_data)
                response.cache_info = {
                    "matched": True,
                    "score": 1.0,
                    "source": "redis",
                    "cache_hit": True,
                    "point_id": None,
                }

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
                    response.cache_info = {
                        "matched": True,
                        "score": 1.0,
                        "rerank_score": 1.0,
                        "source": "qdrant",
                        "cache_hit": True,
                        "point_id": point_id,
                        "query": prompt_text,
                        "accepted": True,
                    }
                    response_dump = response.model_dump(exclude={"cache_info"})

                    # Cache this exact match in Redis
                    if self.redis_service:
                        logger.info(
                            f"Saving Qdrant exact search query matched response in Redis for {exact_hash}"
                        )
                        await self.redis_service.set(exact_hash, response_dump)

                # 3. Try Semantic Match if Exact misses
                if not cached_payload:
                    vector = await self.embedding_service.get_embedding_async(
                        f"search_query: {prompt_text}"
                    )
                    tenant_id = request.user or "anonymous"
                    # now = int(time.time())
                    # expires_at = now - (
                    #     7 * 24 * 60 * 60
                    # )
                    filter_payload = {
                        "model": request.model,
                        "tenant_id": tenant_id,
                        "cacheable": True,
                        "cache_key_version": "v1",
                        # "temperature": request.temperature,
                        # "expires_at": expires_at
                    }
                    # value of threshold can be adjusted based on the desired level of similarity for semantic matches. A higher threshold means stricter matching, while a lower threshold allows for more lenient matches.
                    # threshold = 1.0
                    # threshold = 0.95
                    # threshold = 0.90
                    # threshold = 0.85
                    threshold = 0.80
                    matches = self.qdrant_service.query_points(
                        vector, filter_payload=filter_payload, limit=10
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
                        rerank_score = top_match.get("rerank_score", 0.0)
                        is_accepted = rerank_score > 0.0

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
                                **base_cache_info,
                            }
                            response.cache_info = cache_info
                            response_dump = response.model_dump(exclude={"cache_info"})

                            # Cache this semantic match in Redis as an exact match for future identical queries
                            if self.redis_service:
                                logger.info(
                                    "Saving Qdrant semantic search query matched response in Redis"
                                )
                                await self.redis_service.set(exact_hash, response_dump)
                        else:
                            # Keep it for the LLM fallback
                            miss_cache_info = base_cache_info
            except Exception as e:
                logger.error(f"Qdrant Database read error: {e}")

        if not response:
            if service_name in ["gemini", "google-genai", "google genai"]:
                response = await self._generate_gemini_completion(request)
            elif service_name == "openai":
                response = await self._generate_openai_completion(request)
            elif service_name == "ollama":
                response = await self._generate_ollama_completion(request)
            else:
                logger.warning(
                    f"Unknown service {service_name}. Returning mock response."
                )
                response = self._generate_mock_response(request)

            if response and response.choices:
                try:
                    response_dump = response.model_dump(exclude={"cache_info"})
                    logger.info(
                        f"LLM service generated response successfully for {exact_hash}"
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

                            tenant_id = request.user or "anonymous"
                            embedding_model_name = (
                                self.embedding_service.model_name
                                if self.embedding_service
                                else "unknown"
                            )

                            now = int(time.time())
                            created_at = now
                            expires_at = now + (
                                7 * 24 * 60 * 60
                            )  # 7 days default expiration

                            # Full metadata for production use
                            metadata = {
                                "model": request.model,
                                "embedding_model": embedding_model_name,
                                "prompt_version": "1.0",
                                "embedding_version": "v1",
                                "cacheable": True,
                                "cache_key_version": "v1",
                                "tenant_id": tenant_id,
                                "temperature": request.temperature,
                            }

                            point_id = self.qdrant_service.upsert(
                                vector=vector,
                                prompt=prompt_text,
                                response=response_dump,
                                exact_hash=exact_hash,
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
                        await self.redis_service.set(exact_hash, response_dump)
                except Exception as e:
                    logger.error(f"Redis or Qdrant write error: {e}")

        # self._log_usage(
        #     request=request,
        #     response=response,
        #     service_name=service_name,
        #     is_cached=is_cached,
        #     prompt_text=prompt_text
        # )

        return response

    # def _log_usage(
    #     self,
    #     request: ChatCompletionRequest,
    #     response: ChatCompletionResponse,
    #     service_name: str,
    #     is_cached: bool,
    #     prompt_text: str
    # ):
    #     try:
    #         with SessionLocal() as db:
    #             usage_svc = UsageService(db)
    #             usage = response.usage

    #             prompt_tokens = usage.prompt_tokens if usage else 0
    #             completion_tokens = usage.completion_tokens if usage else 0
    #             total_tokens = usage.total_tokens if usage else 0

    #             # Basic cost calc (can be improved later)
    #             cost = 0.0

    #             output_text = ""
    #             if response.choices and len(response.choices) > 0:
    #                 output_text = response.choices[0].message.content

    #             usage_svc.log_usage(
    #                 model=request.model,
    #                 service_name=service_name,
    #                 prompt_tokens=prompt_tokens,
    #                 completion_tokens=completion_tokens,
    #                 total_tokens=total_tokens,
    #                 cost=cost,
    #                 is_cached=is_cached,
    #                 input_text=prompt_text,
    #                 output_text=output_text
    #             )
    #     except Exception as e:
    #         logger.error(f"Failed to log usage to database: {e}")

    async def _generate_ollama_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        url = f"{self.ollama_base_url}/v1/chat/completions"
        headers: dict[str, str] = {"Content-Type": "application/json"}

        payload_data = request.model_dump(exclude_unset=True)
        if "service_name" in payload_data:
            del payload_data["service_name"]

        try:
            async with httpx.AsyncClient() as client:
                logger.info(f"Forwarding request to local Ollama ({request.model})...")
                response = await client.post(
                    url, json=payload_data, headers=headers, timeout=120.0
                )
                response.raise_for_status()
                data = response.json()
                return ChatCompletionResponse(**data)
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Ollama returned HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise HTTPException(status_code=502, detail="Ollama error")
        except Exception as e:
            logger.error(f"Failed to call Ollama: {e}")
            raise HTTPException(status_code=502, detail="Internal Gateway Error")

    async def _generate_gemini_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        if not self.gemini_client:
            logger.warning(
                "No GEMINI_API_KEY or google-genai package not found. Returning mock response."
            )
            return self._generate_mock_response(request)

        logger.info(f"Forwarding request to Gemini ({request.model})...")

        contents = []
        system_instruction = None
        for msg in request.messages:
            if msg.role == "system":
                system_instruction = msg.content
            else:
                role = "user" if msg.role == "user" else "model"
                contents.append(
                    types.Content(
                        role=role, parts=[types.Part.from_text(text=msg.content)]
                    )
                )

        config_kwargs = {}
        if request.temperature is not None:
            config_kwargs["temperature"] = request.temperature
        if request.top_p is not None:
            config_kwargs["top_p"] = request.top_p
        if request.max_tokens is not None:
            config_kwargs["max_output_tokens"] = request.max_tokens
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        config = types.GenerateContentConfig(**config_kwargs)

        try:
            response = await self.gemini_client.aio.models.generate_content(
                model=request.model, contents=contents, config=config
            )

            prompt_tokens = (
                response.usage_metadata.prompt_token_count
                if response.usage_metadata
                else 0
            )
            completion_tokens = (
                response.usage_metadata.candidates_token_count
                if response.usage_metadata
                else 0
            )
            total_tokens = (
                response.usage_metadata.total_token_count
                if response.usage_metadata
                else 0
            )

            return ChatCompletionResponse(
                id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
                created=int(time.time()),
                model=request.model,
                choices=[
                    Choice(
                        index=0,
                        message=ChatMessage(
                            role="assistant", content=response.text or ""
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                ),
            )
        except Exception as e:
            logger.error(f"Failed to call Gemini API: {e}")
            raise HTTPException(status_code=502, detail="Internal Gateway Error")

    async def _generate_openai_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        if not self.openai_api_key:
            logger.warning("No OPENAI_API_KEY set. Returning mock response.")
            return self._generate_mock_response(request)

        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }

        # Remove service_name before sending to OpenAI
        payload_data = request.model_dump(exclude_unset=True)
        if "service_name" in payload_data:
            del payload_data["service_name"]

        try:
            async with httpx.AsyncClient() as client:
                logger.info(f"Forwarding request to upstream LLM ({request.model})...")
                response = await client.post(
                    self.openai_base_url,
                    json=payload_data,
                    headers=headers,
                    timeout=60.0,
                )
                response.raise_for_status()
                data = response.json()
                return ChatCompletionResponse(**data)
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Upstream API returned HTTP error: {e.response.status_code} - {e.response.text}"
            )
            raise HTTPException(status_code=502, detail="Upstream LLM error")
        except Exception as e:
            logger.error(f"Failed to call upstream LLM: {e}")
            raise HTTPException(status_code=502, detail="Internal Gateway Error")

    def _generate_mock_response(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=request.model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content="This is a mock response from the LLM Gateway.",
                    ),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        )
