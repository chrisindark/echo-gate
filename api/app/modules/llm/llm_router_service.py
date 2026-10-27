import asyncio
import hashlib
import json
import logging
import math
import random
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Coroutine
from typing import Any

from fastapi import HTTPException
from qdrant_client.http import models

from app.core.bulkhead import BulkheadLimitExceededException, bulkhead_manager
from app.core.circuit_breaker import (
    CircuitBreakerOpenException,
    circuit_breaker_registry,
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
    JUDGE_PROMPT_VERSION,
    MOCK_RESPONSE_TEXT,
    PROMPT_VERSION,
    RERANKER_VERSION,
    SCOPE_HIERARCHY,
    SPARSE_EMBEDDING_VERSION,
)
from app.core.degradation import (
    cache_degradation_scope,
    mark_cache_degraded,
    resolve_degradation_state,
)
from app.core.error_classification import classify_provider_error
from app.core.logger import log_latency
from app.core.retry import execute_with_retry
from app.core.telemetry import (
    ECHOGATE_CACHE_HIT,
    ECHOGATE_CONVERSATION_ID,
    ECHOGATE_DEGRADATION_STATE,
    ECHOGATE_EXACT_HASH,
    ECHOGATE_SESSION_ID,
    ECHOGATE_TENANT_ID,
    ECHOGATE_USER_ID,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_REQUEST_TOP_P,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_COMPLETION_TOKENS,
    GEN_AI_USAGE_PROMPT_TOKENS,
    GEN_AI_USAGE_TOTAL_TOKENS,
    SPAN_CACHE_QDRANT_EXACT,
    SPAN_CACHE_REDIS,
    SPAN_GENERATE_COMPLETION,
    SPAN_GENERATE_STREAM,
    SPAN_PROVIDER_CALL,
    trace_span,
)
from app.modules.chat.chat_schema import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    StreamAccumulator,
    response_to_stream_chunks,
)
from app.modules.embedding.embedding_service import EmbeddingService
from app.modules.gateway_requests.gateway_requests_schema import (
    EvaluationStatus,
    GatewayRequestLogCreate,
    RoutingDecision,
)
from app.modules.gateway_requests.gateway_requests_service import GatewayRequestsService
from app.modules.inference.inference_client import InferenceUnavailable
from app.modules.intent_classifier.entity_extractor_service import (
    EntityExtractorService,
)
from app.modules.intent_classifier.intent_classifier_service import (
    IntentClassifierService,
)
from app.modules.intent_classifier.intent_schema import IntentEnum
from app.modules.llm.cache_confidence import CacheConfidenceEvaluator
from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.llm.model_registry import ModelTarget, model_registry
from app.modules.llm_quota.llm_quota_service import LlmQuotaService
from app.modules.llm_usage.llm_usage_schema import LlmUsageLogCreate
from app.modules.llm_usage.llm_usage_service import LlmUsageService
from app.modules.qdrant.llm_cache_collection_service import LlmCacheCollectionService
from app.modules.qdrant.qdrant_schema import CacheScope
from app.modules.redis.redis_service import RedisService
from app.modules.reranking.reranker_service import RerankerService
from app.modules.shadow_cache.shadow_cache_schema import (
    ShadowCacheEvaluationCreate,
    ShadowMatchType,
)
from app.modules.shadow_cache.shadow_cache_service import ShadowCacheService
from app.modules.verifier.judge_service import JudgeService

logger = logging.getLogger(__name__)


def is_mock_response(response: ChatCompletionResponse | None) -> bool:
    if not response or not response.choices:
        return False
    if response.system_fingerprint == "mock":
        return True
    first_choice = response.choices[0]
    if (
        first_choice.message
        and first_choice.message.content
        and first_choice.message.content.strip() == MOCK_RESPONSE_TEXT.strip()
    ):
        return True
    return False


def _chunk_has_content(chunk: ChatCompletionChunk) -> bool:
    """Whether a streamed chunk already committed user-visible output.

    Failover is only safe while the client has received nothing it would have to
    unsee: role-only deltas and usage-only chunks can be followed by another
    provider's chunk, but content, reasoning, or tool calls cannot.
    """
    for choice in chunk.choices or []:
        delta = choice.delta
        if delta is None:
            continue
        if delta.content or delta.reasoning_content or delta.tool_calls:
            return True
    return False


class LlmRouterService:
    def __init__(
        self,
        llm_provider_service: LlmProviderService,
        embedding_service: EmbeddingService,
        llm_cache_service: LlmCacheCollectionService,
        redis_service: RedisService,
        reranker_service: RerankerService,
        intent_classifier_service: IntentClassifierService,
        entity_extractor_service: EntityExtractorService,
        llm_usage_service: LlmUsageService,
        llm_quota_service: LlmQuotaService | None = None,
        gateway_requests_service: GatewayRequestsService | None = None,
        shadow_cache_service: ShadowCacheService | None = None,
        judge_service: JudgeService | None = None,
    ) -> None:
        self.llm_provider_service = llm_provider_service
        self.embedding_service = embedding_service
        self.llm_cache_service = llm_cache_service
        self.redis_service = redis_service
        self.reranker_service = reranker_service
        self.intent_classifier_service = intent_classifier_service
        self.entity_extractor_service = entity_extractor_service
        self.llm_usage_service = llm_usage_service
        self.llm_quota_service = llm_quota_service
        self.gateway_requests_service = gateway_requests_service
        self.shadow_cache_service = shadow_cache_service
        self.judge_service = judge_service
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._embedding_cache: OrderedDict[
            str, tuple[list[float] | None, dict[str, list] | None]
        ] = OrderedDict()
        self._max_embedding_cache_size = 1000

    def clear_embedding_cache(self) -> None:
        self._embedding_cache.clear()

    def _create_background_task(
        self, coro: Coroutine[Any, Any, Any]
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def shutdown(self) -> None:
        """
        Close the service in the dependencies shutdown event, waiting for any background tasks to finish.
        """
        if self._background_tasks:
            logger.info(
                f"Waiting for {len(self._background_tasks)} background tasks to complete..."
            )
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)

    async def _call_provider_with_retry(
        self,
        request: ChatCompletionRequest,
        service_name: str,
        max_retries: int | None = None,
        timeout_seconds: float | None = None,
        fallback_service: str | None = None,
        fallback_model: str | None = None,
        api_key: str | None = None,
        exact_hash: str | None = None,
        timeout_budget_ms: float | None = None,
    ) -> tuple[ChatCompletionResponse | None, str, str]:
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

        async def attempt_call(svc: str, req: ChatCompletionRequest):
            span_attrs = {
                GEN_AI_SYSTEM: svc,
                GEN_AI_REQUEST_MODEL: req.model,
                GEN_AI_REQUEST_TEMPERATURE: req.temperature,
                GEN_AI_REQUEST_MAX_TOKENS: req.max_tokens,
            }
            with trace_span(SPAN_PROVIDER_CALL, span_attrs) as span:
                llm_req = req.model_copy(deep=True)
                for key in ["session_id", "conversation_id"]:
                    if hasattr(llm_req, key):
                        delattr(llm_req, key)

                if svc in ["gemini", "google-genai"]:
                    res = await self.llm_provider_service.generate_gemini_completion(
                        llm_req
                    )
                elif svc == "openai":
                    res = await self.llm_provider_service.generate_openai_completion(
                        llm_req
                    )
                elif svc == "groq":
                    res = await self.llm_provider_service.generate_groq_completion(
                        llm_req
                    )
                elif svc == "openrouter":
                    res = (
                        await self.llm_provider_service.generate_openrouter_completion(
                            llm_req
                        )
                    )
                elif svc == "ollama":
                    res = await self.llm_provider_service.generate_ollama_completion(
                        llm_req
                    )
                elif svc in ["claude", "anthropic"]:
                    res = await self.llm_provider_service.generate_claude_completion(
                        llm_req
                    )
                else:
                    logger.error(f"Unsupported LLM service: {svc}")
                    raise HTTPException(
                        status_code=400, detail=f"Unsupported LLM service: {svc}"
                    )
                if span.is_recording() and hasattr(res, "usage") and res.usage:
                    span.set_attribute(
                        GEN_AI_USAGE_PROMPT_TOKENS, res.usage.prompt_tokens
                    )
                    span.set_attribute(
                        GEN_AI_USAGE_COMPLETION_TOKENS, res.usage.completion_tokens
                    )
                    span.set_attribute(
                        GEN_AI_USAGE_TOTAL_TOKENS, res.usage.total_tokens
                    )
                return res

        t_budget_start = time.monotonic()
        t_primary_start = time.perf_counter()

        # Build candidate target list: intelligent alias resolution or primary + fallback
        if model_registry.is_alias(request.model):
            candidates = model_registry.resolve_targets(service_name, request.model)
        else:
            candidates = [ModelTarget(service=service_name, model=request.model)]
            if str(fallback).lower() == "true" and fallback_service:
                candidates.append(
                    ModelTarget(
                        service=fallback_service,
                        model=fallback_model or request.model,
                    )
                )

        last_exception: BaseException | None = None

        for idx, candidate in enumerate(candidates):
            # 1. Timeout budget propagation check
            if timeout_budget_ms is not None:
                elapsed_s = time.monotonic() - t_budget_start
                remaining_s = (timeout_budget_ms / 1000.0) - elapsed_s
                if remaining_s <= 0.05:
                    raise HTTPException(
                        status_code=504,
                        detail="Request timeout budget exceeded before upstream dispatch.",
                    )
                current_timeout = min(timeout_seconds, max(0.1, remaining_s))
            else:
                current_timeout = timeout_seconds

            # 2. Circuit breaker check
            breaker = circuit_breaker_registry.get_breaker(candidate.service)
            if not breaker.can_execute():
                logger.warning(
                    f"Circuit breaker for provider '{candidate.service}' is OPEN. Fast-skipping."
                )
                if idx < len(candidates) - 1:
                    continue
                raise CircuitBreakerOpenException(
                    candidate.service, breaker.remaining_recovery_time()
                )

            # 3. Bulkhead acquisition & dispatch
            target_req = request.model_copy(deep=True)
            target_req.model = candidate.model

            try:
                async with bulkhead_manager.acquire(
                    candidate.service,
                    timeout=getattr(config, "BULKHEAD_ACQUIRE_TIMEOUT_SECONDS", 0.0),
                ):
                    resp = await execute_with_retry(
                        attempt_call,
                        candidate.service,
                        target_req,
                        timeout_seconds=current_timeout,
                        max_retries=max_retries if idx == 0 else 0,
                    )
                    breaker.record_success()
                    used_model = (
                        resp.model if (resp and resp.model) else candidate.model
                    )
                    degradation = "none" if idx == 0 else "provider_fallback"
                    if resp:
                        resp._degradation_state = degradation
                    return resp, candidate.service, used_model
            except BulkheadLimitExceededException as bhe:
                last_exception = bhe
                if idx < len(candidates) - 1:
                    logger.warning(
                        f"Bulkhead saturated for '{candidate.service}'. Trying next candidate."
                    )
                    continue
                raise bhe
            except Exception as e:
                breaker.record_failure(e)
                last_exception = e

                # Client error fast fail (400, 401, 403)
                if (
                    isinstance(e, HTTPException)
                    and e.status_code in (400, 401, 403)
                    and not model_registry.is_alias(request.model)
                ):
                    logger.error(
                        f"Fatal error {e.status_code} from {candidate.service}. Failing immediately."
                    )
                    raise e

                primary_status = e.status_code if isinstance(e, HTTPException) else 500
                primary_err = str(e.detail) if isinstance(e, HTTPException) else str(e)
                self._create_background_task(
                    self._log_usage(
                        request=request,
                        response=None,
                        service_name=candidate.service,
                        start_time=t_primary_start,
                        exact_hash=exact_hash or self._generate_cache_key(request),
                        api_key=api_key,
                        model=candidate.model,
                        is_success=False,
                        status_code=primary_status,
                        error_message=primary_err,
                    )
                )

                if idx < len(candidates) - 1:
                    logger.warning(
                        f"Target {candidate.service}/{candidate.model} failed: {e}. Trying next candidate."
                    )
                    continue
            finally:
                # Release the HALF_OPEN probe permit on every non-dispatch path
                # (bulkhead shed, budget abort, cancellation). A resolved probe
                # already cleared it in record_success/record_failure.
                breaker.release_probe()

        # All candidates exhausted: classify structured error
        last_svc = candidates[-1].service if candidates else service_name
        classified = classify_provider_error(
            last_exception or RuntimeError("All provider targets failed"), last_svc
        )
        raise HTTPException(
            status_code=classified["status_code"],
            detail={
                "message": classified["message"],
                "error_code": classified["error_code"],
                "provider": last_svc,
            },
            headers=self._retry_after_header(candidates, classified) or None,
        )

    def _retry_after_header(
        self,
        candidates: list[ModelTarget],
        classified: dict[str, Any],
    ) -> dict[str, str]:
        """`Retry-After` for a terminal upstream failure, or nothing if unusable.

        Every candidate failed, so the honest wait is the longest circuit-breaker
        recovery window still counting down: retrying before it expires just hits
        the same open breakers. With no open breaker the upstream is merely
        unhealthy right now, so advertise the configured default backoff. Errors
        that are not retryable at all (a 400 from the provider) carry no header --
        telling a client to retry a request that will fail again only wastes its
        budget.
        """
        if not classified.get("retryable"):
            return {}

        recovery = (
            max(
                (
                    circuit_breaker_registry.get_breaker(
                        candidate.service
                    ).remaining_recovery_time()
                )
                for candidate in candidates
            )
            if candidates
            else 0.0
        )
        seconds = (
            max(1, math.ceil(recovery))
            if recovery > 0
            else max(1, int(getattr(config, "RETRY_AFTER_DEFAULT_SECONDS", 5)))
        )
        return {"Retry-After": str(seconds)}

    async def _stream_from_provider_with_fallback(
        self,
        request: ChatCompletionRequest,
        service_name: str,
        fallback_service: str | None = None,
        fallback_model: str | None = None,
        api_key: str | None = None,
        exact_hash: str | None = None,
        timeout_budget_ms: float | None = None,
    ) -> AsyncIterator[tuple[ChatCompletionChunk, str, str]]:
        """
        Streams chunks from the candidate providers (alias targets or primary+fallback).
        Fast-skips providers whose circuit breaker is OPEN.
        Yields (chunk, actual_service_name, actual_model_name).
        """
        fallback_service = (
            fallback_service
            if fallback_service is not None
            else config.FALLBACK_LLM_SERVICE
        )
        fallback_model = (
            fallback_model if fallback_model is not None else config.FALLBACK_LLM_MODEL
        )
        fallback = config.USE_FALLBACK_LLM

        def clean_req(
            req: ChatCompletionRequest, model: str | None = None
        ) -> ChatCompletionRequest:
            llm_req = req.model_copy(deep=True)
            if model:
                llm_req.model = model
            for key in ["session_id", "conversation_id"]:
                if hasattr(llm_req, key):
                    delattr(llm_req, key)
            return llm_req

        t_primary_start = time.perf_counter()
        t_budget_start = time.monotonic()

        # Build candidate target list: intelligent alias resolution or primary + fallback
        if model_registry.is_alias(request.model):
            candidates = model_registry.resolve_targets(service_name, request.model)
        else:
            candidates = [ModelTarget(service=service_name, model=request.model)]
            if str(fallback).lower() == "true" and fallback_service:
                candidates.append(
                    ModelTarget(
                        service=fallback_service,
                        model=fallback_model or request.model,
                    )
                )

        last_exception: BaseException | None = None

        for idx, candidate in enumerate(candidates):
            # 1. Budget check
            if timeout_budget_ms is not None:
                elapsed_s = time.monotonic() - t_budget_start
                remaining_s = (timeout_budget_ms / 1000.0) - elapsed_s
                if remaining_s <= 0.05:
                    raise HTTPException(
                        status_code=504,
                        detail="Request timeout budget exceeded before streaming dispatch.",
                    )

            # 2. Circuit breaker check
            breaker = circuit_breaker_registry.get_breaker(candidate.service)
            if not breaker.can_execute():
                logger.warning(
                    f"Circuit breaker for provider '{candidate.service}' is OPEN. Fast-skipping stream candidate."
                )
                if idx < len(candidates) - 1:
                    continue
                raise CircuitBreakerOpenException(
                    candidate.service, breaker.remaining_recovery_time()
                )

            # 3. Bulkhead acquisition & stream dispatch
            try:
                async with bulkhead_manager.acquire(
                    candidate.service,
                    timeout=getattr(config, "BULKHEAD_ACQUIRE_TIMEOUT_SECONDS", 0.0),
                ):
                    candidate_req = clean_req(request, model=candidate.model)
                    actual_service = candidate.service
                    actual_model = candidate.model

                    candidate_stream = self.llm_provider_service.generate_stream(
                        candidate_req, service_name=candidate.service
                    )
                    candidate_iter = candidate_stream.__aiter__()
                    first_chunk = None
                    content_emitted = False

                    try:
                        first_chunk = await candidate_iter.__anext__()
                    except StopAsyncIteration:
                        breaker.record_success()
                        return
                    except Exception as e:
                        breaker.record_failure(e)
                        last_exception = e

                        if (
                            isinstance(e, HTTPException)
                            and e.status_code in (400, 401, 403)
                            and not model_registry.is_alias(request.model)
                        ):
                            logger.error(
                                f"Fatal error {e.status_code} from streaming {candidate.service}. Failing immediately."
                            )
                            raise e

                        primary_status = (
                            e.status_code if isinstance(e, HTTPException) else 500
                        )
                        primary_err = (
                            str(e.detail) if isinstance(e, HTTPException) else str(e)
                        )
                        self._create_background_task(
                            self._log_usage(
                                request=request,
                                response=None,
                                service_name=candidate.service,
                                start_time=t_primary_start,
                                exact_hash=exact_hash
                                or self._generate_cache_key(request),
                                api_key=api_key,
                                model=candidate.model,
                                is_success=False,
                                status_code=primary_status,
                                error_message=primary_err,
                            )
                        )

                        if idx < len(candidates) - 1:
                            logger.warning(
                                f"Streaming candidate {candidate.service} failed before first chunk: {e}. Trying next."
                            )
                            continue

                        # Last candidate failed
                        classified = classify_provider_error(e, candidate.service)
                        raise HTTPException(
                            status_code=classified["status_code"],
                            detail={
                                "message": classified["message"],
                                "error_code": classified["error_code"],
                                "provider": candidate.service,
                            },
                            headers=self._retry_after_header(candidates, classified)
                            or None,
                        )

                    # First chunk successfully received!
                    if first_chunk:
                        if first_chunk.model:
                            actual_model = first_chunk.model
                        # Tag the chunk with the provider-path degradation state so
                        # the controller can emit the right response header before
                        # the SSE body starts.
                        first_chunk._degradation_state = (
                            "none" if idx == 0 else "provider_fallback"
                        )
                        content_emitted = content_emitted or _chunk_has_content(
                            first_chunk
                        )
                        yield first_chunk, actual_service, actual_model

                    try:
                        async for chunk in candidate_iter:
                            if chunk.model:
                                actual_model = chunk.model
                            content_emitted = content_emitted or _chunk_has_content(
                                chunk
                            )
                            yield chunk, actual_service, actual_model
                    except Exception as e:
                        # A provider can die after the response has started. That is
                        # still a provider failure, so it counts against the breaker
                        # even though the failure cannot always be routed around.
                        breaker.record_failure(e)
                        last_exception = e
                        self._create_background_task(
                            self._log_usage(
                                request=request,
                                response=None,
                                service_name=candidate.service,
                                start_time=t_primary_start,
                                exact_hash=exact_hash
                                or self._generate_cache_key(request),
                                api_key=api_key,
                                model=candidate.model,
                                is_success=False,
                                status_code=(
                                    e.status_code
                                    if isinstance(e, HTTPException)
                                    else 500
                                ),
                                error_message=(
                                    str(e.detail)
                                    if isinstance(e, HTTPException)
                                    else str(e)
                                ),
                            )
                        )

                        # Nothing user-visible was sent yet: the next candidate can
                        # still answer, which is what the pre-first-chunk path does.
                        if not content_emitted and idx < len(candidates) - 1:
                            logger.warning(
                                f"Streaming candidate {candidate.service} broke "
                                f"mid-stream before any content: {e}. Trying next."
                            )
                            continue

                        logger.error(
                            f"Streaming candidate {candidate.service} failed after "
                            f"{'content was' if content_emitted else 'the stream was'} "
                            f"emitted: {e}. Cannot fail over safely."
                        )
                        classified = classify_provider_error(e, candidate.service)
                        raise HTTPException(
                            status_code=classified["status_code"],
                            detail={
                                "message": classified["message"],
                                "error_code": classified["error_code"],
                                "provider": candidate.service,
                            },
                            # Content already went out, so the client decides whether
                            # to retry; if this failure just tripped the breaker the
                            # hint is its recovery window.
                            headers=self._retry_after_header(candidates, classified)
                            or None,
                        ) from e

                    # Only a stream that ran to completion proves the provider is
                    # healthy; the first chunk alone does not.
                    breaker.record_success()
                    return

            except BulkheadLimitExceededException as bhe:
                last_exception = bhe
                if idx < len(candidates) - 1:
                    logger.warning(
                        f"Bulkhead saturated for streaming '{candidate.service}'. Trying next."
                    )
                    continue
                raise bhe
            finally:
                # See _call_provider_with_retry: never leave a HALF_OPEN probe
                # permit claimed when no chunk was ever dispatched.
                breaker.release_probe()

        last_svc = candidates[-1].service if candidates else service_name
        classified = classify_provider_error(
            last_exception or RuntimeError("All streaming candidates failed"), last_svc
        )
        raise HTTPException(
            status_code=classified["status_code"],
            detail={
                "message": classified["message"],
                "error_code": classified["error_code"],
                "provider": last_svc,
            },
            headers=self._retry_after_header(candidates, classified) or None,
        )

    def _generate_cache_key(self, request: ChatCompletionRequest) -> str:
        # use the version keys in the cache key in future to invalidate older cache keys
        # embedding version
        # cache key version
        messages_list = []
        for msg in request.messages:
            msg_dict: dict[str, Any] = {
                "role": msg.role,
                "content": msg.content,
            }
            if msg.reasoning_content:
                msg_dict["reasoning_content"] = msg.reasoning_content
            if msg.name:
                msg_dict["name"] = msg.name
            if msg.tool_call_id:
                msg_dict["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls:
                msg_dict["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
            messages_list.append(msg_dict)

        key_dict = {
            "cache_key_version": CACHE_KEY_VERSION,
            "service_name": request.service_name,
            "model": request.model,
            "temperature": request.temperature,
            "user_id": request.user_id,
            "tenant_id": request.tenant_id,
            "session_id": request.session_id,
            "conversation_id": request.conversation_id,
            "response_format": request.response_format,
            "reasoning_effort": request.reasoning_effort,
            "thinking": request.thinking,
            "thinking_budget": request.thinking_budget,
            "max_tokens": request.max_tokens,
            "stop": request.stop,
            "top_p": request.top_p,
            "presence_penalty": request.presence_penalty,
            "frequency_penalty": request.frequency_penalty,
            "logit_bias": request.logit_bias,
            "tools": (
                [t.model_dump() for t in request.tools] if request.tools else None
            ),
            "tool_choice": (
                request.tool_choice
                if isinstance(request.tool_choice, (str, int, float, bool, type(None)))
                else json.dumps(request.tool_choice, sort_keys=True)
            ),
            "messages": messages_list,
        }
        key_str = json.dumps(key_dict, sort_keys=True)
        return hashlib.sha256(key_str.encode()).hexdigest()

    async def _get_embeddings(
        self, text: str, get_sparse: bool = True, prefix: str = ""
    ) -> tuple[list[float] | None, dict[str, list] | None]:
        if not text:
            return None, None

        cache_key = f"{prefix}:{get_sparse}:{hashlib.sha256(text.encode()).hexdigest()}"
        if cache_key in self._embedding_cache:
            self._embedding_cache.move_to_end(cache_key)
            return self._embedding_cache[cache_key]

        vec = await self.embedding_service.get_embedding_async(f"{prefix}{text}")
        if get_sparse:
            sparse = await asyncio.to_thread(
                self.embedding_service.get_sparse_embedding, text
            )
        else:
            sparse = None

        res = (vec, sparse)
        self._embedding_cache[cache_key] = res
        if len(self._embedding_cache) > self._max_embedding_cache_size:
            self._embedding_cache.popitem(last=False)
        return res

    def _prepare_prompts(self, request: ChatCompletionRequest) -> tuple[str, str, str]:
        prompt_parts = []
        for msg in request.messages:
            parts = []
            if msg.content:
                parts.append(msg.content)
            if msg.tool_calls:
                tc_str = ",".join(
                    [
                        f"{tc.function.name}({tc.function.arguments})"
                        for tc in msg.tool_calls
                    ]
                )
                parts.append(f"[tool_calls: {tc_str}]")
            if msg.tool_call_id:
                parts.append(f"[call_id: {msg.tool_call_id}]")
            content_desc = " ".join(parts) if parts else ""
            prompt_parts.append(f"{msg.role}: {content_desc}")

        prompt_text = "|\n".join(prompt_parts)
        prompt_text = f"model:{request.model}|{prompt_text}"

        system_messages = [
            msg.content
            for msg in request.messages
            if msg.role == "system" and msg.content
        ]
        system_prompt = "\n".join(system_messages) if system_messages else ""

        user_messages = [
            msg.content
            for msg in request.messages
            if msg.role == "user" and msg.content
        ]
        user_prompt = "\n".join(user_messages) if user_messages else ""

        return prompt_text, system_prompt, user_prompt

    async def get_exact_match(
        self, request: ChatCompletionRequest
    ) -> tuple[dict | None, str | None]:
        exact_hash = self._generate_cache_key(request)
        tenant_id = request.tenant_id
        if self.llm_cache_service:
            try:
                return await asyncio.to_thread(
                    self.llm_cache_service.search_exact,
                    exact_hash=exact_hash,
                    tenant_id=tenant_id,
                )
            except Exception:
                logger.exception("Qdrant exact search error")
                mark_cache_degraded("qdrant_exact_read_failed")
        return None, None

    async def evaluate_semantic_cache(
        self, request: ChatCompletionRequest, force: bool = False
    ) -> dict:
        prompt_text, system_prompt, user_prompt = self._prepare_prompts(request)
        logger.debug(f"User prompt: {user_prompt}")

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
        # Pipeline component versions (config-driven)
        embedding_version = EMBEDDING_VERSION
        cache_key_version = CACHE_KEY_VERSION
        prompt_version = PROMPT_VERSION
        now = int(time.time())

        semantic_start = time.perf_counter()
        breakdown: dict[str, float] = {}
        result: dict[str, Any] = {
            "response": None,
            "cache_info": None,
            "miss_cache_info": None,
            "intent": None,
            "time_sensitivity": 0.0,
            "matches": [],
            "eligible_matches": [],
            "latency_breakdown": breakdown,
            "embeddings": None,
        }

        use_qdrant_semantic_matching = config.cache_layer_enabled("qdrant_semantic")
        if use_qdrant_semantic_matching is False and not force:
            logger.info(
                "Qdrant semantic matching is disabled, skipping semantic search"
            )
            return result

        if not self.llm_cache_service:
            logger.info(
                "Qdrant cache service is not available, skipping semantic search"
            )
            return result

        if request.tools:
            logger.info(
                "Request contains tools/function declarations; bypassing semantic cache to ensure deterministic tool execution"
            )
            return result

        intent = None
        time_sensitivity = 0.0
        core_operation = None
        core_subject = None
        subject_modifier = None
        action_modifier = None

        try:
            breakdown["intent_classify_ms"] = 0.0

            t_emb_start = time.perf_counter()
            prompt_vector, _ = await self._get_embeddings(
                prompt_text, False, EMBEDDING_QUERY_PREFIX
            )
            system_vector, system_sparse = await self._get_embeddings(
                system_prompt, True, EMBEDDING_QUERY_PREFIX
            )
            user_vector, user_sparse = await self._get_embeddings(
                user_prompt, True, EMBEDDING_QUERY_PREFIX
            )
            t_emb_end = time.perf_counter()
            breakdown["embedding_gen_ms"] = round((t_emb_end - t_emb_start) * 1000, 2)

            result["embeddings"] = {
                "prompt_vector": prompt_vector,
                "prompt_sparse": None,
                "system_vector": system_vector,
                "system_sparse": system_sparse,
                "user_vector": user_vector,
                "user_sparse": user_sparse,
            }

            must_conditions = [
                models.FieldCondition(
                    key="prompt_version", match=models.MatchValue(value=prompt_version)
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

            if prompt_vector:
                t_dense_start = time.perf_counter()
                dense_matches = await asyncio.to_thread(
                    self.llm_cache_service.query_points_dense,
                    vector=prompt_vector,
                    using="prompt_embedding",
                    query_filter=query_filter,
                    limit=1,
                )
                t_dense_end = time.perf_counter()
                breakdown["qdrant_dense_ms"] = round(
                    (t_dense_end - t_dense_start) * 1000, 2
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
                    if (
                        top_dense_match["score"]
                        >= config.QDRANT_DENSE_SEARCH_HIGH_THRESHOLD
                    ):
                        logger.info(
                            f"Fast path bypass triggered with dense score {top_dense_match['score']}"
                        )
                        matched_score = top_dense_match["score"]
                        top_prompt = top_dense_match["payload"].get("prompt", "")

                        dense_intent = (
                            intent.value
                            if intent
                            else (
                                top_dense_match["payload"].get("intent")
                                or IntentEnum.GENERAL_QUERY.value
                            )
                        )

                        cache_info = {
                            "matched": True,
                            "score": matched_score,
                            "rerank_score": matched_score,
                            "source": "qdrant_fast_path",
                            "cache_hit": True,
                            "point_id": top_dense_match["id"],
                            "intent": dense_intent,
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

                            use_redis_exact_matching = config.cache_layer_enabled(
                                "redis"
                            )
                            if use_redis_exact_matching is True:
                                expires_at = top_dense_match["payload"].get(
                                    "expires_at"
                                )
                                ttl = (
                                    max(0, expires_at - int(time.time()))
                                    if expires_at
                                    else config.DEFAULT_CACHE_TTL_SECONDS
                                )
                                await self._save_to_redis_cache(
                                    exact_hash=exact_hash,
                                    response=response,
                                    log_message="Saving Qdrant fast path matched response in Redis",
                                    ttl=ttl,
                                    save_to_redis=use_redis_exact_matching,
                                    point_id=top_dense_match["id"],
                                )
                        return result

            t_rrf_start = time.perf_counter()
            matches = await asyncio.to_thread(
                self.llm_cache_service.query_points_rrf,
                system_prompt_vector=system_vector,
                system_prompt_sparse=system_sparse,
                user_prompt_vector=user_vector,
                user_prompt_sparse=user_sparse,
                filter_payload=None,
                query_filter=query_filter,
                limit=config.QDRANT_SEARCH_LIMIT,
            )
            t_rrf_end = time.perf_counter()
            breakdown["qdrant_rrf_ms"] = round((t_rrf_end - t_rrf_start) * 1000, 2)

            # Lowered threshold to retrieve a wider pool of candidates for the Reranker
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
                    match
                    for match in matches
                    if match["score"] >= config.QDRANT_SPARSE_SEARCH_HIGH_THRESHOLD
                ]
                result["eligible_matches"] = eligible_matches

                if eligible_matches:
                    t_intent_start = time.perf_counter()
                    intent_result = (
                        await self.intent_classifier_service.classify_intent(request)
                    )
                    t_intent_end = time.perf_counter()
                    breakdown["intent_classify_ms"] = round(
                        (t_intent_end - t_intent_start) * 1000, 2
                    )
                    if not intent_result:
                        logger.warning(
                            "Intent classification unavailable or timed out. Degrading to semantic cache miss."
                        )
                        return result

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

                    request_entities = (
                        self.entity_extractor_service.get_qdrant_entity_tags(
                            user_prompt
                        )
                    )

                    t_rerank_start = time.perf_counter()
                    matches_reranked = await asyncio.to_thread(
                        self.reranker_service.rerank,
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
                    t_rerank_end = time.perf_counter()
                    breakdown["rerank_ms"] = round(
                        (t_rerank_end - t_rerank_start) * 1000, 2
                    )

                    if not matches_reranked:
                        logger.info(
                            "No matches found after reranking. Skipping cache storage."
                        )
                        return result

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
                        "score_breakdown": top_match.get("score_breakdown"),
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
                        response = ChatCompletionResponse(
                            **top_match["payload"]["response"]
                        )
                        if is_mock_response(response):
                            logger.warning(
                                "Stale mock response found in Qdrant semantic search candidate. Treating as cache miss."
                            )
                            result["miss_cache_info"] = base_cache_info
                        else:
                            logger.info(
                                f"Serving response from Qdrant semantic search query with threshold {config.QDRANT_SPARSE_SEARCH_HIGH_THRESHOLD}, score {matched_score}, rerank_score {rerank_score}."
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
                            ttl = self.intent_classifier_service.calculate_ttl(
                                intent, time_sensitivity
                            )
                            use_redis_exact_matching = config.cache_layer_enabled(
                                "redis"
                            )
                            if use_redis_exact_matching is True:
                                await self._save_to_redis_cache(
                                    exact_hash=exact_hash,
                                    response=response,
                                    log_message="Saving Qdrant semantic search query matched response in Redis",
                                    ttl=ttl,
                                    save_to_redis=use_redis_exact_matching,
                                    point_id=top_match["id"],
                                )

                            result["response"] = response
                            result["cache_info"] = cache_info
                    else:
                        result["miss_cache_info"] = base_cache_info

                return result
            return result
        except InferenceUnavailable as exc:
            # The embedding/model engine is down. That is a cache-layer failure,
            # not a request failure: fall through to the provider exactly as a
            # cache miss would, and record a precise degradation reason instead
            # of reporting it as a Qdrant error.
            logger.warning(
                "inference platform unavailable; skipping semantic cache: %s", exc
            )
            mark_cache_degraded("inference_unavailable")
            return result
        except Exception:
            logger.exception("Qdrant Database read error")
            mark_cache_degraded("qdrant_semantic_read_failed")
            return result
        finally:
            semantic_end = time.perf_counter()
            breakdown["cache_lookup_total_ms"] = round(
                (semantic_end - semantic_start) * 1000, 2
            )

    async def save_to_qdrant_cache(
        self,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse,
        intent: IntentEnum | None = None,
        time_sensitivity: float = 0.0,
        core_operation: str | None = None,
        core_subject: str | None = None,
        subject_modifier: str | None = None,
        action_modifier: str | None = None,
        service_name: str | None = None,
        model: str | None = None,
        save_to_qdrant: bool = False,
        embeddings: dict[str, Any] | None = None,
        force: bool = False,
    ) -> tuple[str | None, int | None]:
        if save_to_qdrant is False and not force:
            return None, None

        if not self.llm_cache_service:
            return None, None

        if is_mock_response(response):
            logger.warning("Skipping Qdrant cache save for mock response")
            return None, None

        prompt_text, system_prompt, user_prompt = self._prepare_prompts(request)
        exact_hash = self._generate_cache_key(request)
        tenant_id = request.tenant_id
        user_id = request.user_id
        session_id = request.session_id
        conversation_id = request.conversation_id
        effective_service_name = (
            service_name or request.service_name or config.DEFAULT_LLM_SERVICE
        )
        effective_model = model or (
            response.model if response and response.model else request.model
        )

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
            # Reuse pre-computed embeddings if available and prefixes match
            if embeddings and EMBEDDING_QUERY_PREFIX == EMBEDDING_DOCUMENT_PREFIX:
                logger.info("Reusing pre-computed embeddings for Qdrant cache storage")
                system_vec = embeddings.get("system_vector")
                system_sparse = embeddings.get("system_sparse")
                user_vec = embeddings.get("user_vector")
                user_sparse = embeddings.get("user_sparse")
                prompt_vec = embeddings.get("prompt_vector")
                prompt_sparse = embeddings.get("prompt_sparse")
            else:
                system_vec, system_sparse = None, None
                user_vec, user_sparse = None, None
                prompt_vec, prompt_sparse = None, None

            if system_vec is None and system_prompt:
                system_vec, system_sparse = await self._get_embeddings(
                    system_prompt, True, EMBEDDING_DOCUMENT_PREFIX
                )
            if user_vec is None and user_prompt:
                user_vec, user_sparse = await self._get_embeddings(
                    user_prompt, True, EMBEDDING_DOCUMENT_PREFIX
                )
            if prompt_vec is None and prompt_text:
                prompt_vec, prompt_sparse = await self._get_embeddings(
                    prompt_text, False, EMBEDDING_DOCUMENT_PREFIX
                )
            logger.info("Embeddings ready for Qdrant cache storage")

            if intent is None:
                intent_result = await self.intent_classifier_service.classify_intent(
                    request
                )
                if intent_result:
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
                else:
                    intent = IntentEnum.GENERAL_QUERY
                    time_sensitivity = 0.0
                    core_operation = ""
                    core_subject = ""
                    subject_modifier = None
                    action_modifier = None
                    logger.warning(
                        f"Intent classification unavailable for prompt {exact_hash}. Falling back to default intent {intent.value}."
                    )

            entities, max_scope_enum = (
                self.entity_extractor_service.get_tags_and_max_scope(
                    user_prompt or prompt_text
                )
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

            ttl = self.intent_classifier_service.calculate_ttl(intent, time_sensitivity)
            cacheable = True
            if ttl is not None:
                expires_at = now + ttl
            else:
                expires_at = now + CACHE_NEGATIVE_TTL_OFFSET
                cacheable = False

            metadata = {
                "prompt_version": PROMPT_VERSION,
                "intent_prompt_version": INTENT_PROMPT_VERSION,
                "sparse_embedding_version": SPARSE_EMBEDDING_VERSION,
                "reranker_version": RERANKER_VERSION,
                "judge_prompt_version": JUDGE_PROMPT_VERSION,
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

            point_id = await asyncio.to_thread(
                self.llm_cache_service.upsert,
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
                model=effective_model,
                service_name=effective_service_name,
                scope=actual_scope,
                entities=entities,
                time_sensitivity=time_sensitivity,
                intent=intent.value,
                core_operation=core_operation,
                core_subject=core_subject,
                embedding_model=embedding_model_name,
                embedding_version=embedding_version,
                cacheable=cacheable,
                cache_key_version=cache_key_version,
                created_at=created_at,
                expires_at=expires_at,
                metadata=metadata,
            )
            if point_id is not None:
                logger.info(
                    f"Generated embedding saved successfully in Qdrant for {exact_hash}"
                )
            return point_id, ttl
        except Exception:
            logger.exception("Embedding creation error")
            return None, None

    async def _get_redis_exact_match(
        self, exact_hash: str, service_name: str, force: bool = False
    ) -> ChatCompletionResponse | None:
        with trace_span(SPAN_CACHE_REDIS, {ECHOGATE_EXACT_HASH: exact_hash}) as span:
            use_redis_exact_matching = config.cache_layer_enabled("redis")
            if use_redis_exact_matching is False and not force:
                logger.info("Redis exact matching and semantic matching is disabled")
                return None

            if not self.redis_service:
                return None

            cached_data = await self.redis_service.get(exact_hash)
            if cached_data:
                try:
                    data = json.loads(cached_data)
                except Exception:
                    logger.error(
                        f"Failed to parse cached JSON from Redis for {exact_hash}"
                    )
                    return None

                point_id = None
                if (
                    isinstance(data, dict)
                    and "response" in data
                    and isinstance(data["response"], dict)
                ):
                    response = ChatCompletionResponse(**data["response"])
                    point_id = data.get("point_id")
                elif isinstance(data, dict):
                    response = ChatCompletionResponse(**data)
                    point_id = data.get("point_id")
                else:
                    return None

                if is_mock_response(response):
                    logger.warning(
                        f"Found cached mock response in Redis for {exact_hash}. Purging and ignoring."
                    )
                    await self.redis_service.delete(exact_hash)
                    return None

                logger.info(
                    f"Serving response from Redis Cache for {exact_hash} using {service_name} (point_id: {point_id})"
                )
                response.cache_info = {
                    "matched": True,
                    "score": 0.0,
                    "source": "redis",
                    "cache_hit": True,
                    "point_id": point_id,
                }
                if span.is_recording():
                    span.set_attribute(ECHOGATE_CACHE_HIT, True)
                return response
            if span.is_recording():
                span.set_attribute(ECHOGATE_CACHE_HIT, False)
            return None

    async def _get_qdrant_exact_match(
        self,
        exact_hash: str,
        tenant_id: str | None,
        prompt_text: str,
        force: bool = False,
    ) -> tuple[ChatCompletionResponse | None, str | None, int | None]:
        with trace_span(
            SPAN_CACHE_QDRANT_EXACT, {ECHOGATE_EXACT_HASH: exact_hash}
        ) as span:
            use_qdrant_exact_matching = config.cache_layer_enabled("qdrant_exact")
            if use_qdrant_exact_matching is False and not force:
                logger.info("Qdrant exact matching is disabled, skipping exact search")
                return None, None, None

            if not self.llm_cache_service:
                logger.info(
                    "Qdrant cache service is not available, skipping exact search"
                )
                return None, None, None

            cached_payload, point_id = await asyncio.to_thread(
                self.llm_cache_service.search_exact,
                exact_hash=exact_hash,
                tenant_id=tenant_id,
            )
            if cached_payload and "response" in cached_payload:
                response = ChatCompletionResponse(**cached_payload["response"])
                if is_mock_response(response):
                    logger.warning(
                        f"Found cached mock response in Qdrant exact search for {exact_hash}. Ignoring."
                    )
                    return None, None, None

                logger.info(
                    f"Serving response from Qdrant exact search query for {exact_hash}"
                )
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
                expires_at = cached_payload.get("expires_at")
                remaining_ttl = (
                    max(0, expires_at - int(time.time())) if expires_at else None
                )
                if span.is_recording():
                    span.set_attribute(ECHOGATE_CACHE_HIT, True)
                return response, point_id, remaining_ttl
            if span.is_recording():
                span.set_attribute(ECHOGATE_CACHE_HIT, False)
            return None, None, None

    async def _save_to_redis_cache(
        self,
        exact_hash: str,
        response: ChatCompletionResponse,
        log_message: str,
        ttl: int | None = -1,
        save_to_redis: bool = False,
        point_id: str | None = None,
        force: bool = False,
    ) -> None:
        if save_to_redis is False and not force:
            return

        if is_mock_response(response):
            logger.warning(
                f"Skipping Redis cache save for mock response for {exact_hash}"
            )
            return

        if ttl is None:
            logger.info(
                f"Skipping Redis cache for {exact_hash} due to time sensitivity (uncacheable)"
            )
            return

        if self.redis_service:
            logger.info(log_message)
            response_dump = response.model_dump(exclude={"cache_info"})

            resolved_point_id = point_id
            if (
                not resolved_point_id
                and hasattr(response, "cache_info")
                and response.cache_info
            ):
                resolved_point_id = response.cache_info.get("point_id")

            payload = {
                "response": response_dump,
                "point_id": resolved_point_id,
            }
            pass_ttl = None if ttl == -1 else ttl
            await self.redis_service.set(exact_hash, json.dumps(payload), ttl=pass_ttl)

    async def _background_save_cache(
        self,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse,
        exact_hash: str,
        service_name: str,
        model: str,
        intent: IntentEnum | None = None,
        time_sensitivity: float = 0.0,
        core_operation: str | None = None,
        core_subject: str | None = None,
        subject_modifier: str | None = None,
        action_modifier: str | None = None,
        embeddings: dict[str, Any] | None = None,
        force: bool = False,
    ) -> None:
        """Persist a response to the Qdrant and Redis cache layers.

        ``force`` is used by the shadow pipeline, which writes every layer
        regardless of the serving-path flags so the corpus is warm by the time
        the cache is switched on.
        """
        try:
            point_id = None
            ttl = None
            use_qdrant_exact_matching = (
                config.cache_layer_enabled("qdrant_exact") or force
            )
            if use_qdrant_exact_matching:
                point_id, ttl = await self.save_to_qdrant_cache(
                    request=request,
                    response=response,
                    intent=intent,
                    time_sensitivity=time_sensitivity,
                    core_operation=core_operation,
                    core_subject=core_subject,
                    subject_modifier=subject_modifier,
                    action_modifier=action_modifier,
                    service_name=service_name,
                    model=model,
                    save_to_qdrant=use_qdrant_exact_matching,
                    embeddings=embeddings,
                )
            use_redis_exact_matching = config.cache_layer_enabled("redis") or force
            if use_redis_exact_matching:
                ttl = config.REDIS_TTL
                await self._save_to_redis_cache(
                    exact_hash,
                    response,
                    "Saving LLM generated response in Redis",
                    ttl=ttl,
                    save_to_redis=use_redis_exact_matching,
                    point_id=point_id,
                )
            logger.info(
                f"Background cache write completed successfully for {exact_hash} (point_id: {point_id})"
            )
        except Exception:
            logger.exception(f"Background cache write failed for {exact_hash}")

    def _should_run_shadow_evaluation(
        self, response: ChatCompletionResponse | None
    ) -> bool:
        """Whether this request should be replayed through the cache pipeline."""
        if config.CACHE_MODE != "shadow" or not self.shadow_cache_service:
            return False
        if response is None or not response.choices or is_mock_response(response):
            return False
        if (response.cache_info or {}).get("overflowed"):
            # A truncated stream is not the answer that would have been cached.
            return False
        sample_rate = config.SHADOW_SAMPLE_RATE
        return sample_rate >= 1.0 or random.random() < sample_rate

    @staticmethod
    async def _await_gateway_log_id(
        gateway_log_task: asyncio.Task[int | None] | None,
    ) -> int | None:
        """Wait for the sibling trace-log task so the shadow row can link to it."""
        if gateway_log_task is None:
            return None
        try:
            return await gateway_log_task
        except Exception:
            return None

    @staticmethod
    def _response_text(response: ChatCompletionResponse | None) -> str | None:
        """The user-visible answer text, including tool calls when present."""
        if not response or not response.choices:
            return None
        message = response.choices[0].message
        if message is None:
            return None
        if message.content:
            return message.content
        if message.tool_calls:
            return json.dumps([tc.model_dump() for tc in message.tool_calls])
        return None

    async def _background_shadow_evaluate(
        self,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse,
        exact_hash: str,
        service_name: str,
        model: str,
        gateway_log_task: asyncio.Task[int | None] | None = None,
    ) -> None:
        """Replay the cache pipeline for a request that was served by a provider.

        Order matters: the lookup runs first and the write second, otherwise this
        request's own freshly written entry would be the match it reports. Every
        failure is swallowed — this task observes traffic and must never affect
        it.
        """
        if not self.shadow_cache_service:
            return

        try:
            request_id = await self._await_gateway_log_id(gateway_log_task)
            prompt_text, _, user_prompt = self._prepare_prompts(request)

            lookup_start = time.perf_counter()
            breakdown: dict[str, Any] = {}
            semantic_result: dict[str, Any] = {}
            cached_response: ChatCompletionResponse | None = None
            match_type = ShadowMatchType.MISS
            point_id: str | None = None

            t_redis = time.perf_counter()
            cached_response = await self._get_redis_exact_match(
                exact_hash, service_name, force=True
            )
            breakdown["redis_exact_ms"] = round(
                (time.perf_counter() - t_redis) * 1000, 2
            )

            if cached_response:
                match_type = ShadowMatchType.REDIS_EXACT
                point_id = (cached_response.cache_info or {}).get("point_id")
            else:
                t_qdrant = time.perf_counter()
                (
                    cached_response,
                    point_id,
                    _remaining_ttl,
                ) = await self._get_qdrant_exact_match(
                    exact_hash, request.tenant_id, prompt_text, force=True
                )
                breakdown["qdrant_exact_ms"] = round(
                    (time.perf_counter() - t_qdrant) * 1000, 2
                )
                if cached_response:
                    match_type = ShadowMatchType.QDRANT_EXACT
                else:
                    semantic_result = await self.evaluate_semantic_cache(
                        request, force=True
                    )
                    breakdown.update(semantic_result.get("latency_breakdown") or {})
                    cached_response = semantic_result.get("response")
                    if cached_response:
                        source = (cached_response.cache_info or {}).get("source")
                        match_type = (
                            ShadowMatchType.QDRANT_FAST
                            if source == "qdrant_fast_path"
                            else ShadowMatchType.QDRANT_SEMANTIC
                        )
                        point_id = (cached_response.cache_info or {}).get("point_id")

            lookup_ms = round((time.perf_counter() - lookup_start) * 1000, 2)
            breakdown["cache_lookup_total_ms"] = lookup_ms

            cache_info = (
                dict(cached_response.cache_info or {}) if cached_response else {}
            )
            intent_value = cache_info.get("intent") or getattr(
                semantic_result.get("intent"), "value", None
            )
            entities, _max_scope = self.entity_extractor_service.get_tags_and_max_scope(
                user_prompt or prompt_text
            )
            cached_text = self._response_text(cached_response)
            actual_text = self._response_text(response)
            would_have_hit = cached_response is not None
            provider_ms = (
                ((response.cache_info or {}).get("latency_breakdown") or {}).get(
                    "provider_ms"
                )
                if response.cache_info
                else None
            )

            # Build the corpus for activation: write every layer regardless of the
            # serving-path flags, now that the lookup has been recorded.
            await self._background_save_cache(
                request=request,
                response=response,
                exact_hash=exact_hash,
                service_name=service_name,
                model=model,
                intent=semantic_result.get("intent"),
                time_sensitivity=semantic_result.get("time_sensitivity", 0.0) or 0.0,
                core_operation=semantic_result.get("core_operation"),
                core_subject=semantic_result.get("core_subject"),
                subject_modifier=semantic_result.get("subject_modifier"),
                action_modifier=semantic_result.get("action_modifier"),
                embeddings=semantic_result.get("embeddings"),
                force=True,
            )

            is_equivalent: bool | None = None
            if would_have_hit and cached_text and actual_text:
                if cached_text.strip() == actual_text.strip():
                    is_equivalent = True
                elif config.SHADOW_JUDGE_ENABLED and self.judge_service:
                    is_equivalent = await self.judge_service.evaluate_equivalence(
                        query_text=prompt_text,
                        cached_response=cached_text,
                        actual_response=actual_text,
                    )

            projected_latency_saved_ms = None
            if would_have_hit and provider_ms is not None:
                projected_latency_saved_ms = max(0, int(round(provider_ms - lookup_ms)))

            rerank_score = cache_info.get("rerank_score")
            if rerank_score is None:
                rerank_score = cache_info.get("top_1_rerank_score")

            evaluation = ShadowCacheEvaluationCreate(
                request_id=request_id,
                exact_hash=exact_hash,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                model=model,
                provider=service_name,
                would_have_hit=would_have_hit,
                match_type=match_type,
                point_id=point_id,
                match_score=cache_info.get("score"),
                rerank_score=rerank_score,
                score_breakdown=cache_info.get("score_breakdown"),
                cached_response_text=cached_text,
                actual_response_text=actual_text,
                is_equivalent=is_equivalent,
                intent=intent_value,
                entities=entities or None,
                latency_breakdown=breakdown,
                provider_latency_ms=(
                    int(provider_ms) if provider_ms is not None else None
                ),
                cache_lookup_latency_ms=int(lookup_ms),
                projected_latency_saved_ms=projected_latency_saved_ms,
            )
            await asyncio.to_thread(
                self.shadow_cache_service.log_evaluation, evaluation
            )
            logger.info(
                "Shadow evaluation for %s: match_type=%s would_have_hit=%s "
                "equivalent=%s cache_lookup_ms=%.2f",
                exact_hash,
                match_type.value,
                would_have_hit,
                is_equivalent,
                lookup_ms,
            )
        except Exception:
            logger.exception("Shadow cache evaluation failed for %s", exact_hash)

    async def _log_usage(
        self,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse | None,
        service_name: str,
        start_time: float,
        exact_hash: str,
        api_key: str | None = None,
        model: str | None = None,
        is_success: bool = True,
        status_code: int = 200,
        error_message: str | None = None,
    ) -> None:
        try:
            end_time = time.perf_counter()
            latency_ms = int((end_time - start_time) * 1000)
            is_cache_hit = (
                response.cache_info.get("cache_hit", False)
                if response
                and hasattr(response, "cache_info")
                and isinstance(response.cache_info, dict)
                else False
            )

            prompt_text, _, _ = self._prepare_prompts(request)
            input_text = prompt_text
            output_text = (
                response.choices[0].message.content
                if response
                and response.choices
                and len(response.choices) > 0
                and response.choices[0].message
                else None
            )
            if (
                output_text is None
                and response
                and response.choices
                and len(response.choices) > 0
                and response.choices[0].message
                and response.choices[0].message.tool_calls
            ):
                output_text = json.dumps(
                    [tc.model_dump() for tc in response.choices[0].message.tool_calls]
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
            if response and response.choices and len(response.choices) > 0:
                finish_reason = response.choices[0].finish_reason
            elif not is_success:
                finish_reason = "error"

            effective_model = model or (
                response.model if response and response.model else request.model
            )

            log_data = LlmUsageLogCreate(
                service_name=service_name,
                model=effective_model,
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
                cost_calculated=not is_success,
                latency_ms=latency_ms,
                status_code=status_code,
                is_success=is_success,
                error_message=error_message,
                finish_reason=finish_reason,
            )

            await asyncio.to_thread(
                self.llm_usage_service.create_llm_usage_log, log_data
            )
            logger.info(
                f"LLM usage logged successfully for {exact_hash} (success={is_success})"
            )

            # Record tokens for TPM limit
            if self.llm_quota_service and api_key and total_tokens > 0 and is_success:
                try:
                    await self.llm_quota_service.record_tokens(
                        api_key=api_key,
                        total_tokens=total_tokens,
                        provider=service_name,
                        model=effective_model,
                        user_id=request.user_id,
                    )
                except Exception:
                    logger.exception("Failed to record TPM usage")
        except Exception:
            logger.exception("Failed to log LLM usage")

    async def _log_gateway_request(
        self,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse | None,
        service_name: str,
        start_time: float,
        exact_hash: str,
        model: str | None = None,
        latency_breakdown: dict[str, Any] | None = None,
        error_message: str | None = None,
        routing_decision: RoutingDecision | None = None,
        intent: str | None = None,
        core_operation: str | None = None,
        core_subject: str | None = None,
        subject_modifier: str | None = None,
        action_modifier: str | None = None,
    ) -> int | None:
        """Persist a gateway request trace and return its row id.

        The id is returned so the shadow evaluator can link its row to the trace
        of the request it observed; callers that do not need it may ignore it.
        """
        if not self.gateway_requests_service:
            return None

        try:
            end_time = time.perf_counter()
            latency_ms = int((end_time - start_time) * 1000)
            prompt_text, _, _ = self._prepare_prompts(request)

            if routing_decision is not None:
                final_routing_decision = routing_decision
            elif response:
                source = (
                    response.cache_info.get("source", "llm")
                    if hasattr(response, "cache_info") and response.cache_info
                    else "llm"
                )
                final_routing_decision = RoutingDecision.LLM
                if source == "redis":
                    final_routing_decision = RoutingDecision.REDIS_EXACT
                elif source == "qdrant_exact_path":
                    final_routing_decision = RoutingDecision.QDRANT_EXACT
                elif source == "qdrant_semantic_path":
                    final_routing_decision = RoutingDecision.QDRANT_SEMANTIC
                elif source == "qdrant_fast_path":
                    final_routing_decision = RoutingDecision.QDRANT_FAST
            else:
                final_routing_decision = RoutingDecision.LLM

            cache_info = (
                response.cache_info
                if response and hasattr(response, "cache_info") and response.cache_info
                else {}
            )

            output_text = (
                response.choices[0].message.content
                if response
                and response.choices
                and len(response.choices) > 0
                and response.choices[0].message
                else None
            )
            if (
                output_text is None
                and response
                and response.choices
                and len(response.choices) > 0
                and response.choices[0].message
                and response.choices[0].message.tool_calls
            ):
                output_text = json.dumps(
                    [tc.model_dump() for tc in response.choices[0].message.tool_calls]
                )

            effective_model = model or (
                response.model if response and response.model else request.model
            )

            provider_latency_ms = (
                int(latency_breakdown["provider_ms"])
                if latency_breakdown
                and latency_breakdown.get("provider_ms") is not None
                else None
            )
            cache_lookup_latency_ms = (
                int(latency_breakdown["cache_lookup_total_ms"])
                if latency_breakdown
                and latency_breakdown.get("cache_lookup_total_ms") is not None
                else None
            )

            eval_status = (
                EvaluationStatus.SKIPPED if error_message else EvaluationStatus.PENDING
            )

            # Semantic hits set `rerank_score`; semantic misses only set the best
            # candidate's `top_1_rerank_score`. Fall back so the trace always
            # records the composite score that drove the accept/reject decision.
            rerank_score = cache_info.get("rerank_score")
            if rerank_score is None:
                rerank_score = cache_info.get("top_1_rerank_score")

            log_data = GatewayRequestLogCreate(
                query_text=prompt_text,
                response_text=output_text,
                routing_decision=final_routing_decision,
                provider=service_name,
                model=effective_model,
                point_id=cache_info.get("point_id"),
                exact_hash=exact_hash,
                intent=cache_info.get("intent") or intent,
                core_operation=cache_info.get("core_operation") or core_operation,
                core_subject=cache_info.get("core_subject") or core_subject,
                subject_modifier=cache_info.get("subject_modifier") or subject_modifier,
                action_modifier=cache_info.get("action_modifier") or action_modifier,
                rerank_score=rerank_score,
                score_breakdown=cache_info.get("score_breakdown"),
                latency_ms=latency_ms,
                provider_latency_ms=provider_latency_ms,
                cache_lookup_latency_ms=cache_lookup_latency_ms,
                latency_breakdown=latency_breakdown,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                conversation_id=request.conversation_id,
                error_message=error_message,
                evaluation_status=eval_status,
            )
            row = await asyncio.to_thread(
                self.gateway_requests_service.log_request, log_data
            )
            return getattr(row, "id", None)
        except Exception:
            logger.exception("Failed to log gateway request")
            return None

    @log_latency()
    async def generate_completion(
        self,
        request: ChatCompletionRequest,
        api_key: str | None = None,
        timeout_budget_ms: float | None = None,
    ) -> ChatCompletionResponse:
        """Resolve a completion and stamp its degradation state.

        The cache-degradation scope wraps the whole pipeline so a fail-open cache
        read anywhere below (Redis, Qdrant exact, semantic search) is reported on
        the response instead of only in the logs.
        """
        service_name = request.service_name or config.DEFAULT_LLM_SERVICE
        span_attrs = {
            GEN_AI_SYSTEM: service_name,
            GEN_AI_REQUEST_MODEL: request.model,
            GEN_AI_REQUEST_TEMPERATURE: request.temperature,
            GEN_AI_REQUEST_TOP_P: request.top_p,
            GEN_AI_REQUEST_MAX_TOKENS: request.max_tokens,
            ECHOGATE_TENANT_ID: request.tenant_id,
            ECHOGATE_USER_ID: request.user_id,
            ECHOGATE_SESSION_ID: request.session_id,
            ECHOGATE_CONVERSATION_ID: request.conversation_id,
        }
        with trace_span(SPAN_GENERATE_COMPLETION, span_attrs) as span:
            with cache_degradation_scope():
                response = await self._generate_completion_impl(
                    request, api_key, timeout_budget_ms
                )
                served_from_cache = bool((response.cache_info or {}).get("matched"))
                degradation = resolve_degradation_state(
                    getattr(response, "_degradation_state", None),
                    served_from_cache=served_from_cache,
                )
                response._degradation_state = degradation
                if span.is_recording():
                    span.set_attribute(ECHOGATE_CACHE_HIT, served_from_cache)
                    span.set_attribute(ECHOGATE_DEGRADATION_STATE, degradation)
                    if response.usage:
                        span.set_attribute(
                            GEN_AI_USAGE_PROMPT_TOKENS, response.usage.prompt_tokens
                        )
                        span.set_attribute(
                            GEN_AI_USAGE_COMPLETION_TOKENS,
                            response.usage.completion_tokens,
                        )
                        span.set_attribute(
                            GEN_AI_USAGE_TOTAL_TOKENS, response.usage.total_tokens
                        )
                return response

    async def _generate_completion_impl(
        self,
        request: ChatCompletionRequest,
        api_key: str | None = None,
        timeout_budget_ms: float | None = None,
    ) -> ChatCompletionResponse:
        start_time = time.perf_counter()
        breakdown: dict[str, Any] = {}
        service_name = request.service_name or config.DEFAULT_LLM_SERVICE
        logger.info(f"Generating completion for service {service_name}")

        prompt_text, _, _ = self._prepare_prompts(request)
        exact_hash = self._generate_cache_key(request)
        tenant_id = request.tenant_id

        actual_service = service_name
        actual_model = request.model
        provider_error: str | None = None
        provider_exception: BaseException | None = None
        response: ChatCompletionResponse | None = None
        intent: IntentEnum | None = None
        time_sensitivity = 0.0
        core_operation: str | None = None
        core_subject: str | None = None
        subject_modifier: str | None = None
        action_modifier: str | None = None
        miss_cache_info = None
        embeddings = None
        shadow_mode = config.CACHE_MODE == "shadow"

        try:
            if shadow_mode:
                # Observation-only: no cache read may touch the serving path, and
                # the response is served exclusively by a provider. The cache
                # pipeline (lookup + write) runs after the response is returned.
                logger.info(
                    "CACHE_MODE=shadow: bypassing serving-time cache lookups for %s",
                    exact_hash,
                )
            else:
                # 1. Try exact match from Redis first
                t_redis_start = time.perf_counter()
                response = await self._get_redis_exact_match(exact_hash, service_name)
                t_redis_end = time.perf_counter()
                breakdown["redis_exact_ms"] = round(
                    (t_redis_end - t_redis_start) * 1000, 2
                )
                if response:
                    return response

                # 2. Try Exact Match in Qdrant
                t_qdrant_exact_start = time.perf_counter()
                response, point_id, remaining_ttl = await self._get_qdrant_exact_match(
                    exact_hash, tenant_id, prompt_text
                )
                t_qdrant_exact_end = time.perf_counter()
                breakdown["qdrant_exact_ms"] = round(
                    (t_qdrant_exact_end - t_qdrant_exact_start) * 1000, 2
                )
                if response:
                    use_redis_exact_matching = config.cache_layer_enabled("redis")
                    if use_redis_exact_matching:
                        await self._save_to_redis_cache(
                            exact_hash,
                            response,
                            "Saving Qdrant exact search query matched response in Redis",
                            ttl=remaining_ttl,
                            save_to_redis=use_redis_exact_matching,
                            point_id=point_id,
                        )
                    return response

                # 3. Try Semantic Match if Exact misses
                semantic_result = await self.evaluate_semantic_cache(request)
                if "latency_breakdown" in semantic_result:
                    breakdown.update(semantic_result["latency_breakdown"])

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
                embeddings = semantic_result.get("embeddings")

            t_provider_start = time.perf_counter()
            try:
                (
                    response,
                    actual_service,
                    actual_model,
                ) = await self._call_provider_with_retry(
                    request=request,
                    service_name=service_name,
                    max_retries=config.LLM_MAX_RETRIES,
                    timeout_seconds=config.LLM_PROVIDER_TIMEOUT_SECONDS,
                    fallback_service=config.FALLBACK_LLM_SERVICE,
                    fallback_model=config.FALLBACK_LLM_MODEL,
                    api_key=api_key,
                    exact_hash=exact_hash,
                    timeout_budget_ms=timeout_budget_ms,
                )
            except Exception as e:
                provider_error = str(e)
                provider_exception = e
                if (
                    config.USE_FALLBACK_LLM == "true"
                    and config.FALLBACK_LLM_SERVICE
                    and not (
                        isinstance(e, HTTPException)
                        and e.status_code in (400, 401, 403, 500)
                    )
                ):
                    actual_service = config.FALLBACK_LLM_SERVICE
                    if config.FALLBACK_LLM_MODEL:
                        actual_model = config.FALLBACK_LLM_MODEL
                raise
            finally:
                t_provider_end = time.perf_counter()
                breakdown["provider_ms"] = round(
                    (t_provider_end - t_provider_start) * 1000, 2
                )

            if response and response.choices and not is_mock_response(response):
                cache_info = {
                    "matched": False,
                    "score": 0.0,
                    "source": "llm",
                    "cache_hit": False,
                    "point_id": None,
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
                breakdown["cache_write_ms"] = 0.0

                if shadow_mode:
                    # The shadow evaluator performs the cache write itself, after
                    # it has recorded what the lookup would have returned.
                    logger.info(
                        "CACHE_MODE=shadow: deferring cache write for %s to the "
                        "shadow evaluator",
                        exact_hash,
                    )
                else:
                    # Offload cache persistence to background task so client latency is unaffected
                    self._create_background_task(
                        self._background_save_cache(
                            request=request,
                            response=response.model_copy(deep=True),
                            exact_hash=exact_hash,
                            service_name=actual_service,
                            model=actual_model,
                            intent=intent,
                            time_sensitivity=time_sensitivity,
                            core_operation=core_operation,
                            core_subject=core_subject,
                            subject_modifier=subject_modifier,
                            action_modifier=action_modifier,
                            embeddings=embeddings,
                        )
                    )
            elif response and is_mock_response(response):
                logger.warning(
                    f"Mock response generated for {exact_hash}. Skipping cache writes."
                )
                cache_info = {
                    "matched": False,
                    "score": 0.0,
                    "source": "mock",
                    "cache_hit": False,
                    "point_id": None,
                    "intent": intent.value if intent else IntentEnum.EMPTY.value,
                    "query": prompt_text,
                    "accepted": False,
                }
                if miss_cache_info:
                    cache_info.update(miss_cache_info)
                response.cache_info = cache_info

            return response
        except Exception as e:
            if not provider_error:
                provider_error = str(e)
                provider_exception = e
            raise
        finally:
            end_time = time.perf_counter()
            total_duration = round((end_time - start_time) * 1000, 2)
            breakdown["total_ms"] = total_duration

            cache_lookup_total = (
                breakdown.get("redis_exact_ms", 0.0)
                + breakdown.get("qdrant_exact_ms", 0.0)
                + breakdown.get("cache_lookup_total_ms", 0.0)
            )
            breakdown["cache_lookup_total_ms"] = round(cache_lookup_total, 2)

            if response:
                if not hasattr(response, "cache_info") or response.cache_info is None:
                    response.cache_info = {}
                response.cache_info["exact_hash"] = exact_hash
                response.cache_info["latency_breakdown"] = breakdown

                self._create_background_task(
                    self._log_usage(
                        request=request,
                        response=response,
                        service_name=actual_service,
                        start_time=start_time,
                        exact_hash=exact_hash,
                        api_key=api_key,
                        model=actual_model,
                    )
                )
                gateway_log_task = self._create_background_task(
                    self._log_gateway_request(
                        request=request,
                        response=response,
                        service_name=actual_service,
                        start_time=start_time,
                        exact_hash=exact_hash,
                        model=actual_model,
                        latency_breakdown=breakdown,
                    )
                )
                if shadow_mode and self._should_run_shadow_evaluation(response):
                    self._create_background_task(
                        self._background_shadow_evaluate(
                            request=request,
                            response=response.model_copy(deep=True),
                            exact_hash=exact_hash,
                            service_name=actual_service,
                            model=actual_model,
                            gateway_log_task=gateway_log_task,
                        )
                    )
            elif provider_exception is not None:
                status_code = (
                    provider_exception.status_code
                    if isinstance(provider_exception, HTTPException)
                    else 500
                )
                error_detail = (
                    str(provider_exception.detail)
                    if isinstance(provider_exception, HTTPException)
                    else (provider_error or str(provider_exception))
                )
                self._create_background_task(
                    self._log_usage(
                        request=request,
                        response=None,
                        service_name=actual_service,
                        start_time=start_time,
                        exact_hash=exact_hash,
                        api_key=api_key,
                        model=actual_model,
                        is_success=False,
                        status_code=status_code,
                        error_message=error_detail,
                    )
                )
                self._create_background_task(
                    self._log_gateway_request(
                        request=request,
                        response=None,
                        service_name=actual_service,
                        start_time=start_time,
                        exact_hash=exact_hash,
                        model=actual_model,
                        latency_breakdown=breakdown,
                        error_message=error_detail,
                        routing_decision=RoutingDecision.LLM,
                        intent=intent.value
                        if (intent and hasattr(intent, "value"))
                        else None,
                        core_operation=core_operation,
                        core_subject=core_subject,
                        subject_modifier=subject_modifier,
                        action_modifier=action_modifier,
                    )
                )

    async def generate_stream(
        self,
        request: ChatCompletionRequest,
        api_key: str | None = None,
        timeout_budget_ms: float | None = None,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Stream a completion, tagging the first chunk with its degradation state.

        The controller reads that tag before the response headers are sent, so a
        streamed answer served by a fallback provider or after a failed cache read
        no longer reports ``none``.
        """
        service_name = request.service_name or config.DEFAULT_LLM_SERVICE
        span_attrs = {
            GEN_AI_SYSTEM: service_name,
            GEN_AI_REQUEST_MODEL: request.model,
            ECHOGATE_TENANT_ID: request.tenant_id,
            ECHOGATE_USER_ID: request.user_id,
            ECHOGATE_SESSION_ID: request.session_id,
            ECHOGATE_CONVERSATION_ID: request.conversation_id,
        }
        with trace_span(SPAN_GENERATE_STREAM, span_attrs):
            with cache_degradation_scope():
                first = True
                async for chunk in self._generate_stream_impl(
                    request, api_key, timeout_budget_ms
                ):
                    if first:
                        first = False
                        provider_state = getattr(chunk, "_degradation_state", None)
                        chunk._degradation_state = resolve_degradation_state(
                            provider_state,
                            # Untagged first chunks come from the cache paths.
                            served_from_cache=provider_state is None,
                        )
                    yield chunk

    async def _generate_stream_impl(
        self,
        request: ChatCompletionRequest,
        api_key: str | None = None,
        timeout_budget_ms: float | None = None,
    ) -> AsyncIterator[ChatCompletionChunk]:
        start_time = time.perf_counter()
        breakdown: dict[str, Any] = {}
        service_name = request.service_name or config.DEFAULT_LLM_SERVICE
        logger.info(f"Generating streaming completion for service {service_name}")

        prompt_text, _, _ = self._prepare_prompts(request)
        exact_hash = self._generate_cache_key(request)
        tenant_id = request.tenant_id

        actual_service = service_name
        actual_model = request.model
        provider_error: str | None = None
        provider_exception: BaseException | None = None
        response: ChatCompletionResponse | None = None
        intent: IntentEnum | None = None
        time_sensitivity = 0.0
        core_operation: str | None = None
        core_subject: str | None = None
        subject_modifier: str | None = None
        action_modifier: str | None = None
        miss_cache_info = None
        embeddings = None
        shadow_mode = config.CACHE_MODE == "shadow"

        try:
            if shadow_mode:
                logger.info(
                    "CACHE_MODE=shadow: bypassing serving-time cache lookups for %s",
                    exact_hash,
                )
            else:
                # 1. Try exact match from Redis first
                t_redis_start = time.perf_counter()
                response = await self._get_redis_exact_match(exact_hash, service_name)
                t_redis_end = time.perf_counter()
                breakdown["redis_exact_ms"] = round(
                    (t_redis_end - t_redis_start) * 1000, 2
                )
                if response:
                    for chunk in response_to_stream_chunks(response):
                        yield chunk
                    return

                # 2. Try Exact Match in Qdrant
                t_qdrant_exact_start = time.perf_counter()
                response, point_id, remaining_ttl = await self._get_qdrant_exact_match(
                    exact_hash, tenant_id, prompt_text
                )
                t_qdrant_exact_end = time.perf_counter()
                breakdown["qdrant_exact_ms"] = round(
                    (t_qdrant_exact_end - t_qdrant_exact_start) * 1000, 2
                )
                if response:
                    use_redis_exact_matching = config.cache_layer_enabled("redis")
                    if use_redis_exact_matching:
                        await self._save_to_redis_cache(
                            exact_hash,
                            response,
                            "Saving Qdrant exact search query matched response in Redis",
                            ttl=remaining_ttl,
                            save_to_redis=use_redis_exact_matching,
                            point_id=point_id,
                        )
                    for chunk in response_to_stream_chunks(response):
                        yield chunk
                    return

                # 3. Try Semantic Match if Exact misses
                semantic_result = await self.evaluate_semantic_cache(request)
                if "latency_breakdown" in semantic_result:
                    breakdown.update(semantic_result["latency_breakdown"])

                if semantic_result.get("response"):
                    response = semantic_result["response"]
                    for chunk in response_to_stream_chunks(response):
                        yield chunk
                    return

                # Extract intent and other metadata if intent classification has been called already
                intent = semantic_result.get("intent", None)
                time_sensitivity = semantic_result.get("time_sensitivity", 0.0)
                core_operation = semantic_result.get("core_operation", None)
                core_subject = semantic_result.get("core_subject", None)
                subject_modifier = semantic_result.get("subject_modifier", None)
                action_modifier = semantic_result.get("action_modifier", None)
                miss_cache_info = semantic_result.get("miss_cache_info")
                embeddings = semantic_result.get("embeddings")

            # 4. Stream from provider with guarded accumulator
            accumulator = StreamAccumulator(
                model=request.model,
                request_id=f"chatcmpl-{uuid.uuid4().hex}",
                max_content_bytes=config.STREAM_MAX_CONTENT_BYTES,
            )

            t_provider_start = time.perf_counter()
            try:
                stream = self._stream_from_provider_with_fallback(
                    request=request,
                    service_name=service_name,
                    fallback_service=config.FALLBACK_LLM_SERVICE,
                    fallback_model=config.FALLBACK_LLM_MODEL,
                    api_key=api_key,
                    exact_hash=exact_hash,
                    timeout_budget_ms=timeout_budget_ms,
                )
                async for chunk, chunk_svc, chunk_model in stream:
                    actual_service = chunk_svc
                    actual_model = chunk_model
                    accumulator.add_chunk(chunk)
                    yield chunk
            except Exception as e:
                provider_error = str(e)
                provider_exception = e
                if (
                    config.USE_FALLBACK_LLM == "true"
                    and config.FALLBACK_LLM_SERVICE
                    and not (
                        isinstance(e, HTTPException)
                        and e.status_code in (400, 401, 403, 500)
                    )
                ):
                    actual_service = config.FALLBACK_LLM_SERVICE
                    if config.FALLBACK_LLM_MODEL:
                        actual_model = config.FALLBACK_LLM_MODEL
                raise
            finally:
                t_provider_end = time.perf_counter()
                breakdown["provider_ms"] = round(
                    (t_provider_end - t_provider_start) * 1000, 2
                )

            # Build full response from accumulator
            response = accumulator.build_response()
            if actual_model:
                response.model = actual_model

            if response and response.choices and not is_mock_response(response):
                cache_info = {
                    "matched": False,
                    "score": 0.0,
                    "source": "llm",
                    "cache_hit": False,
                    "point_id": None,
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

                if accumulator.overflowed:
                    logger.warning(
                        f"Stream content exceeded memory guardrail ({accumulator.max_content_bytes} bytes). Skipping cache persistence."
                    )
                    cache_info["overflowed"] = True
                    response.cache_info = cache_info
                else:
                    response.cache_info = cache_info
                    breakdown["cache_write_ms"] = 0.0

                    if shadow_mode:
                        # The shadow evaluator performs the cache write itself,
                        # after recording what the lookup would have returned.
                        logger.info(
                            "CACHE_MODE=shadow: deferring cache write for %s to "
                            "the shadow evaluator",
                            exact_hash,
                        )
                    else:
                        # Offload cache persistence to background task so client latency is unaffected
                        self._create_background_task(
                            self._background_save_cache(
                                request=request,
                                response=response.model_copy(deep=True),
                                exact_hash=exact_hash,
                                service_name=actual_service,
                                model=actual_model,
                                intent=intent,
                                time_sensitivity=time_sensitivity,
                                core_operation=core_operation,
                                core_subject=core_subject,
                                subject_modifier=subject_modifier,
                                action_modifier=action_modifier,
                                embeddings=embeddings,
                            )
                        )
            elif response and is_mock_response(response):
                logger.warning(
                    f"Mock response generated for {exact_hash}. Skipping cache writes."
                )
                cache_info = {
                    "matched": False,
                    "score": 0.0,
                    "source": "mock",
                    "cache_hit": False,
                    "point_id": None,
                    "intent": intent.value if intent else IntentEnum.EMPTY.value,
                    "query": prompt_text,
                    "accepted": False,
                }
                if miss_cache_info:
                    cache_info.update(miss_cache_info)
                response.cache_info = cache_info

            return
        except Exception as e:
            if not provider_error:
                provider_error = str(e)
                provider_exception = e
            raise
        finally:
            end_time = time.perf_counter()
            total_duration = round((end_time - start_time) * 1000, 2)
            breakdown["total_ms"] = total_duration

            cache_lookup_total = (
                breakdown.get("redis_exact_ms", 0.0)
                + breakdown.get("qdrant_exact_ms", 0.0)
                + breakdown.get("cache_lookup_total_ms", 0.0)
            )
            breakdown["cache_lookup_total_ms"] = round(cache_lookup_total, 2)

            if response:
                if not hasattr(response, "cache_info") or response.cache_info is None:
                    response.cache_info = {}
                response.cache_info["exact_hash"] = exact_hash
                response.cache_info["latency_breakdown"] = breakdown

                self._create_background_task(
                    self._log_usage(
                        request=request,
                        response=response,
                        service_name=actual_service,
                        start_time=start_time,
                        exact_hash=exact_hash,
                        api_key=api_key,
                        model=actual_model,
                    )
                )
                gateway_log_task = self._create_background_task(
                    self._log_gateway_request(
                        request=request,
                        response=response,
                        service_name=actual_service,
                        start_time=start_time,
                        exact_hash=exact_hash,
                        model=actual_model,
                        latency_breakdown=breakdown,
                    )
                )
                if shadow_mode and self._should_run_shadow_evaluation(response):
                    self._create_background_task(
                        self._background_shadow_evaluate(
                            request=request,
                            response=response.model_copy(deep=True),
                            exact_hash=exact_hash,
                            service_name=actual_service,
                            model=actual_model,
                            gateway_log_task=gateway_log_task,
                        )
                    )
            elif provider_exception is not None:
                status_code = (
                    provider_exception.status_code
                    if isinstance(provider_exception, HTTPException)
                    else 500
                )
                error_detail = (
                    str(provider_exception.detail)
                    if isinstance(provider_exception, HTTPException)
                    else (provider_error or str(provider_exception))
                )
                self._create_background_task(
                    self._log_usage(
                        request=request,
                        response=None,
                        service_name=actual_service,
                        start_time=start_time,
                        exact_hash=exact_hash,
                        api_key=api_key,
                        model=actual_model,
                        is_success=False,
                        status_code=status_code,
                        error_message=error_detail,
                    )
                )
                self._create_background_task(
                    self._log_gateway_request(
                        request=request,
                        response=None,
                        service_name=actual_service,
                        start_time=start_time,
                        exact_hash=exact_hash,
                        model=actual_model,
                        latency_breakdown=breakdown,
                        error_message=error_detail,
                        routing_decision=RoutingDecision.LLM,
                        intent=intent.value
                        if (intent and hasattr(intent, "value"))
                        else None,
                        core_operation=core_operation,
                        core_subject=core_subject,
                        subject_modifier=subject_modifier,
                        action_modifier=action_modifier,
                    )
                )
