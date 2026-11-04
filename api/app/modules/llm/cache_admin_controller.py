"""Admin cache management endpoints.

Provides cache warmup/pre-seeding, inspection, and eviction for operators.
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.config import config
from app.core.dependencies import get_llm_router_service
from app.core.guards import require_admin_scope
from app.core.responses import BaseAPIResponse
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.chat.chat_schema import ChatCompletionRequest, ChatCompletionResponse
from app.modules.inference.inference_client import InferenceUnavailable

logger = logging.getLogger(__name__)

api_v1_router = APIRouter(prefix="/api/v1/admin/cache", tags=["Cache (Admin)"])

RequireCacheRead = require_admin_scope("cache:read")
RequireCacheWrite = require_admin_scope("cache:write")

# Warmup fans out to embeddings, intent classification, and two cache writes per
# item, so both the batch size and the in-flight concurrency are bounded.
MAX_WARMUP_ITEMS = 200
WARMUP_CONCURRENCY = 5


class CacheWarmupItem(BaseModel):
    """A ``(request, response)`` pair to seed into the cache.

    Deliberately *not* a ``ChatCompletionRequest`` subclass: ``tenant_id`` is a
    private attribute there, so a same-named field would be shadowed by its
    property and silently dropped. ``tenant_id`` is explicit here because cache
    keys are tenant-scoped -- omitting it writes a ``GLOBAL``-scope entry that
    no tenant's exact-match lookup can ever hit.
    """

    request: ChatCompletionRequest
    response: ChatCompletionResponse
    tenant_id: str | None = None


class CacheWarmupRequest(BaseModel):
    items: list[CacheWarmupItem] = Field(default_factory=list)
    force: bool = True


class CacheWarmupResponse(BaseModel):
    queued: int
    succeeded: int
    failed: int


class CacheSearchRequest(BaseModel):
    """Semantic lookup over stored cache entries."""

    query: str = Field(min_length=1, description="Free-text prompt to embed and match")
    limit: int = Field(default=20, ge=1, le=100)
    min_score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Drop hits below this similarity"
    )
    model: str | None = None
    intent: str | None = None
    scope: str | None = None
    tenant_id: str | None = None


class CacheSearchHit(BaseModel):
    """One stored entry, flattened from the Qdrant payload."""

    point_id: str
    score: float | None = None
    prompt: str | None = None
    user_prompt: str | None = None
    model: str | None = None
    intent: str | None = None
    scope: str | None = None
    tenant_id: str | None = None
    exact_hash: str | None = None
    cacheable: bool | None = None
    expires_at: int | None = None
    hit_count: int | None = None


class CacheSearchResponse(BaseModel):
    count: int
    results: list[CacheSearchHit]


class CacheEvictResponse(BaseModel):
    point_id: str
    qdrant_deleted: bool
    redis_deleted: bool | None = None


@api_v1_router.post("/warmup", response_model=BaseAPIResponse[CacheWarmupResponse])
async def warmup_cache(
    body: CacheWarmupRequest,
    identity: AdminKeyIdentity = Depends(RequireCacheWrite),
    router=Depends(get_llm_router_service),
):
    """Pre-seed the cache with request/response pairs.

    Accepts a batch of ``(request, response)`` pairs and writes them to the
    Redis and Qdrant cache layers through the same save path real traffic uses.
    Useful for restoring cache after a reindex or preloading high-traffic
    prompts. ``force`` writes every layer regardless of ``CACHE_MODE`` and the
    per-layer flags, exactly like the shadow evaluator.

    A write that fails is counted in ``failed`` and never fails the request.
    """
    if not body.items:
        raise HTTPException(status_code=400, detail="items cannot be empty")

    if len(body.items) > MAX_WARMUP_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"items cannot contain more than {MAX_WARMUP_ITEMS} entries "
                f"(received {len(body.items)})"
            ),
        )

    semaphore = asyncio.Semaphore(WARMUP_CONCURRENCY)

    async def _warm_item(item: CacheWarmupItem) -> bool:
        async with semaphore:
            try:
                request = item.request
                # Admin-only override: the public API never honours a
                # caller-supplied tenant, but warmup has to reproduce the
                # tenant-scoped key the original traffic produced.
                request._tenant_id = item.tenant_id

                exact_hash = router._generate_cache_key(request)
                return await router._background_save_cache(
                    request=request,
                    response=item.response,
                    exact_hash=exact_hash,
                    service_name=request.service_name or config.DEFAULT_LLM_SERVICE,
                    model=request.model,
                    force=body.force,
                )
            except Exception:
                logger.exception(
                    f"Cache warmup failed for model {item.request.model}"
                )
                return False

    results = await asyncio.gather(*(_warm_item(item) for item in body.items))
    succeeded = sum(1 for ok in results if ok)

    logger.info(
        f"Cache warmup finished: {succeeded}/{len(body.items)} items seeded "
        f"for admin key {identity.key_id}"
    )

    return BaseAPIResponse.success_response(
        CacheWarmupResponse(
            queued=len(body.items),
            succeeded=succeeded,
            failed=len(results) - succeeded,
        )
    )


def _hit_from_match(match: dict[str, Any]) -> CacheSearchHit:
    payload = match.get("payload") or {}
    return CacheSearchHit(
        point_id=match.get("id"),
        score=match.get("score"),
        prompt=payload.get("prompt"),
        user_prompt=payload.get("user_prompt"),
        model=payload.get("model"),
        intent=payload.get("intent"),
        scope=payload.get("scope"),
        tenant_id=payload.get("tenant_id"),
        exact_hash=payload.get("exact_hash"),
        cacheable=payload.get("cacheable"),
        expires_at=payload.get("expires_at"),
        hit_count=payload.get("hit_count"),
    )


@api_v1_router.post("/search", response_model=BaseAPIResponse[CacheSearchResponse])
async def search_cache(
    body: CacheSearchRequest,
    identity: AdminKeyIdentity = Depends(RequireCacheRead),
    router=Depends(get_llm_router_service),
):
    """Find stored cache entries semantically similar to ``query``.

    Mirrors the serving path's embedding and vector name so the hits shown here
    are the entries a real request would be compared against. Deliberately does
    not apply the serving-path filters (``cacheable`` / ``expires_at`` /
    tenant scope), because an inspector has to be able to see the dead and the
    cross-tenant rows too.
    """
    collection = router.llm_cache_service
    if collection is None:
        raise HTTPException(status_code=503, detail="Qdrant cache service is unavailable")

    filters = {
        "model": body.model,
        "intent": body.intent,
        "scope": body.scope,
        "tenant_id": body.tenant_id,
    }
    filter_payload = {key: value for key, value in filters.items() if value is not None}

    try:
        vector, _ = await router._get_embeddings(body.query, False)
    except InferenceUnavailable as exc:
        logger.warning(f"Inference engine unavailable during cache search: {exc}")
        raise HTTPException(
            status_code=503, detail=f"Inference engine unavailable: {exc}"
        ) from exc

    if not vector:
        raise HTTPException(
            status_code=503, detail="Embedding engine returned no vector"
        )

    matches = await asyncio.to_thread(
        collection.query_points_dense,
        vector=vector,
        using="user_prompt_embedding",
        filter_payload=filter_payload or None,
        limit=body.limit,
        score_threshold=body.min_score,
    )

    hits = [_hit_from_match(match) for match in matches]
    logger.info(
        f"Cache inspector search returned {len(hits)} hits for admin key "
        f"{identity.key_id} (filters: {filter_payload or 'none'})"
    )
    return BaseAPIResponse.success_response(
        CacheSearchResponse(count=len(hits), results=hits)
    )


@api_v1_router.delete(
    "/entry/{point_id}", response_model=BaseAPIResponse[CacheEvictResponse]
)
async def evict_cache_entry(
    point_id: str,
    exact_hash: str | None = None,
    identity: AdminKeyIdentity = Depends(RequireCacheWrite),
    router=Depends(get_llm_router_service),
):
    """Evict one cache entry from Qdrant and, when known, from Redis.

    ``exact_hash`` is optional because the Redis copy is keyed by it: pass the
    value returned by ``/search`` to drop both layers, or omit it to delete the
    vector alone. Qdrant deletion is idempotent, so evicting a missing point
    still reports success.
    """
    collection = router.llm_cache_service
    if collection is None:
        raise HTTPException(status_code=503, detail="Qdrant cache service is unavailable")

    qdrant_deleted = await asyncio.to_thread(collection.delete_point, point_id)

    redis_deleted: bool | None = None
    if exact_hash and router.redis_service is not None:
        try:
            await router.redis_service.delete(exact_hash)
            redis_deleted = True
        except Exception:
            logger.exception(f"Redis eviction failed for exact_hash {exact_hash}")
            redis_deleted = False

    logger.info(
        f"Cache eviction for point {point_id}: qdrant={qdrant_deleted}, "
        f"redis={redis_deleted} (admin key {identity.key_id})"
    )

    if not qdrant_deleted:
        raise HTTPException(status_code=500, detail="Qdrant eviction failed")

    return BaseAPIResponse.success_response(
        CacheEvictResponse(
            point_id=point_id,
            qdrant_deleted=qdrant_deleted,
            redis_deleted=redis_deleted,
        )
    )
