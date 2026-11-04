"""Admin cache management endpoints.

Provides cache warmup/pre-seeding, inspection, and eviction for operators.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.core.config import config
from app.core.dependencies import get_llm_router_service
from app.core.guards import require_admin_scope
from app.core.responses import BaseAPIResponse
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.chat.chat_schema import ChatCompletionRequest, ChatCompletionResponse
from app.modules.inference.inference_client import InferenceUnavailable
from app.modules.qdrant.llm_cache_collection_service import MAX_BULK_EVICT_POINTS

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
    """Lookup over stored cache entries: semantic (by prompt) or exact (by hash).

    ``mode`` picks the lookup. Semantic mode needs ``query`` and embeds it;
    exact mode needs ``exact_hash`` and skips the inference engine entirely,
    which is what an operator wants when chasing a specific key from a trace.
    """

    mode: Literal["semantic", "exact"] = "semantic"
    query: str | None = Field(
        default=None, description="Free-text prompt to embed and match (semantic mode)"
    )
    exact_hash: str | None = Field(
        default=None, description="Exact cache key to look up (exact mode)"
    )
    limit: int = Field(default=20, ge=1, le=100)
    min_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Drop semantic hits below this similarity (semantic mode)",
    )
    model: str | None = None
    intent: str | None = None
    scope: str | None = None
    tenant_id: str | None = None

    @model_validator(mode="after")
    def _require_the_mode_input(self) -> "CacheSearchRequest":
        if self.mode == "semantic":
            if not (self.query or "").strip():
                raise ValueError("query is required for semantic search")
        elif not (self.exact_hash or "").strip():
            raise ValueError("exact_hash is required for exact search")
        return self


class CacheSearchHit(BaseModel):
    """One stored entry, flattened from the Qdrant payload."""

    point_id: str
    score: float | None = None
    prompt: str | None = None
    user_prompt: str | None = None
    model: str | None = None
    service_name: str | None = None
    intent: str | None = None
    scope: str | None = None
    tenant_id: str | None = None
    exact_hash: str | None = None
    cacheable: bool | None = None
    created_at: int | None = None
    expires_at: int | None = None
    hit_count: int | None = None

    # Entry metadata that the cache writes into the payload.
    entities: list[str] = Field(default_factory=list)
    time_sensitivity: float | None = None
    core_operation: str | None = None
    core_subject: str | None = None
    prompt_version: str | None = None
    reranker_version: str | None = None
    cache_key_version: str | None = None

    # Value-aware retention metadata.
    provider_cost: float | None = None
    latency_ms: int | None = None
    token_count: int | None = None
    retention_value: float | None = None

    # Latest sampled-audit verdict for this point, when one exists. Rerank and
    # vector scores are query-time values, so they live on the audit row rather
    # than in the payload.
    rerank_score: float | None = None
    vector_similarity: float | None = None
    is_equivalent: bool | None = None
    last_audited_at: datetime | None = None


class CacheSearchResponse(BaseModel):
    count: int
    results: list[CacheSearchHit]


class CacheEvictResponse(BaseModel):
    point_id: str
    qdrant_deleted: bool
    redis_deleted: bool | None = None


class CacheBulkEvictRequest(BaseModel):
    """Filtered, bounded eviction across a whole cache slice.

    At least one filter is required: an empty body must never mean "delete the
    collection". ``dry_run`` defaults to true so the UI can show the match count
    before anything is removed.
    """

    intent: str | None = None
    model: str | None = None
    scope: str | None = None
    tenant_id: str | None = None
    older_than_days: float | None = Field(
        default=None,
        gt=0,
        description="Match entries created more than this many days ago",
    )
    expired_only: bool = Field(
        default=False, description="Match entries whose TTL has already lapsed"
    )
    dry_run: bool = True
    limit: int = Field(default=1000, ge=1, le=MAX_BULK_EVICT_POINTS)
    purge_redis: bool = Field(
        default=True,
        description="Also delete the Redis copy for every matched exact hash",
    )

    @model_validator(mode="after")
    def _require_a_filter(self) -> "CacheBulkEvictRequest":
        if not any(
            (
                self.intent,
                self.model,
                self.scope,
                self.tenant_id,
                self.older_than_days,
                self.expired_only,
            )
        ):
            raise ValueError("at least one filter is required")
        return self


class CacheBulkEvictResponse(BaseModel):
    filters: dict[str, Any]
    matched: int
    deleted: int
    redis_deleted: int
    dry_run: bool


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
                logger.exception(f"Cache warmup failed for model {item.request.model}")
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


def _hit_from_match(
    match: dict[str, Any], evaluated: dict[str, Any] | None = None
) -> CacheSearchHit:
    payload = match.get("payload") or {}
    audit = evaluated or {}
    return CacheSearchHit(
        point_id=match.get("id"),
        score=match.get("score"),
        prompt=payload.get("prompt"),
        user_prompt=payload.get("user_prompt"),
        model=payload.get("model"),
        service_name=payload.get("service_name"),
        intent=payload.get("intent"),
        scope=payload.get("scope"),
        tenant_id=payload.get("tenant_id"),
        exact_hash=payload.get("exact_hash"),
        cacheable=payload.get("cacheable"),
        created_at=payload.get("created_at"),
        expires_at=payload.get("expires_at"),
        hit_count=payload.get("hit_count"),
        entities=payload.get("entities") or [],
        time_sensitivity=payload.get("time_sensitivity"),
        core_operation=payload.get("core_operation"),
        core_subject=payload.get("core_subject"),
        prompt_version=payload.get("prompt_version"),
        reranker_version=payload.get("reranker_version"),
        cache_key_version=payload.get("cache_key_version"),
        provider_cost=payload.get("provider_cost"),
        latency_ms=payload.get("latency_ms"),
        token_count=payload.get("token_count"),
        retention_value=payload.get("retention_value"),
        rerank_score=audit.get("rerank_score"),
        vector_similarity=audit.get("vector_similarity"),
        is_equivalent=audit.get("is_equivalent"),
        last_audited_at=audit.get("created_at"),
    )


async def _latest_audits(router, point_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch audit verdicts for the inspected points, or nothing if unavailable.

    The inspector must keep working when Postgres (or the audit table) is not
    reachable: a missing verdict is a degraded detail, not a failed search.
    """
    service = getattr(router, "cache_audit_service", None)
    if service is None or not point_ids:
        return {}

    try:
        return await asyncio.to_thread(service.get_latest_audit_by_point_ids, point_ids)
    except Exception:
        logger.warning(
            "Cache inspector could not load audit verdicts; continuing without them",
            exc_info=True,
        )
        return {}


async def _semantic_matches(router, body: CacheSearchRequest, filter_payload: dict):
    try:
        vector, _ = await router._get_embeddings(body.query or "", False)
    except InferenceUnavailable as exc:
        logger.warning(f"Inference engine unavailable during cache search: {exc}")
        raise HTTPException(
            status_code=503, detail=f"Inference engine unavailable: {exc}"
        ) from exc

    if not vector:
        raise HTTPException(
            status_code=503, detail="Embedding engine returned no vector"
        )

    return await asyncio.to_thread(
        router.llm_cache_service.query_points_dense,
        vector=vector,
        using="user_prompt_embedding",
        filter_payload=filter_payload or None,
        limit=body.limit,
        score_threshold=body.min_score,
    )


@api_v1_router.post("/search", response_model=BaseAPIResponse[CacheSearchResponse])
async def search_cache(
    body: CacheSearchRequest,
    identity: AdminKeyIdentity = Depends(RequireCacheRead),
    router=Depends(get_llm_router_service),
):
    """Find stored cache entries by prompt (semantic) or key (exact).

    Semantic mode mirrors the serving path's embedding and vector name so the
    hits shown here are the entries a real request would be compared against.
    Exact mode looks the key up directly, which also works when the inference
    engine is down.

    Neither mode applies the serving-path filters (``cacheable`` / ``expires_at``
    / tenant scope), because an inspector has to be able to see the dead and the
    cross-tenant rows too.
    """
    collection = router.llm_cache_service
    if collection is None:
        raise HTTPException(
            status_code=503, detail="Qdrant cache service is unavailable"
        )

    filters = {
        "model": body.model,
        "intent": body.intent,
        "scope": body.scope,
        "tenant_id": body.tenant_id,
    }
    filter_payload = {key: value for key, value in filters.items() if value is not None}

    if body.mode == "exact":
        try:
            matches = await asyncio.to_thread(
                collection.search_by_exact_hash,
                (body.exact_hash or "").strip(),
                filter_payload or None,
                body.limit,
            )
        except Exception:
            logger.exception("Exact-hash cache search failed")
            raise HTTPException(
                status_code=503, detail="Qdrant exact-hash search failed"
            ) from None
    else:
        matches = await _semantic_matches(router, body, filter_payload)

    audits = await _latest_audits(router, [match.get("id") for match in matches])
    hits = [_hit_from_match(match, audits.get(match.get("id"))) for match in matches]
    logger.info(
        f"Cache inspector {body.mode} search returned {len(hits)} hits for admin "
        f"key {identity.key_id} (filters: {filter_payload or 'none'})"
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
        raise HTTPException(
            status_code=503, detail="Qdrant cache service is unavailable"
        )

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


@api_v1_router.post("/evict", response_model=BaseAPIResponse[CacheBulkEvictResponse])
async def bulk_evict_cache(
    body: CacheBulkEvictRequest,
    identity: AdminKeyIdentity = Depends(RequireCacheWrite),
    router=Depends(get_llm_router_service),
):
    """Evict a filtered slice of the cache, from Qdrant and (optionally) Redis.

    Filters are ANDed: ``intent``, ``model``, ``scope``, ``tenant_id``,
    ``older_than_days`` (by ``created_at``) and ``expired_only`` (by
    ``expires_at``). At least one is required, ``dry_run`` defaults to true, and
    the match count is capped by ``limit`` so a single call cannot wipe the
    collection.

    A real sweep also deletes the Redis copy of every matched exact hash unless
    ``purge_redis`` is false; leaving those behind would keep exact hits
    servable after the vectors are gone. Per-key Redis failures are counted, not
    raised, exactly like single-entry eviction.
    """
    collection = router.llm_cache_service
    if collection is None:
        raise HTTPException(
            status_code=503, detail="Qdrant cache service is unavailable"
        )

    filters = {
        "intent": body.intent,
        "model": body.model,
        "scope": body.scope,
        "tenant_id": body.tenant_id,
        "older_than_days": body.older_than_days,
        "expired_only": body.expired_only,
    }
    applied_filters = {
        key: value for key, value in filters.items() if value not in (None, False)
    }

    try:
        plan = await asyncio.to_thread(
            collection.bulk_evict,
            intent=body.intent,
            model=body.model,
            scope=body.scope,
            tenant_id=body.tenant_id,
            older_than_days=body.older_than_days,
            expired_only=body.expired_only,
            limit=body.limit,
            dry_run=body.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("Bulk cache eviction failed")
        raise HTTPException(
            status_code=503, detail="Qdrant bulk eviction failed"
        ) from None

    redis_deleted = 0
    if not body.dry_run and body.purge_redis and router.redis_service is not None:
        semaphore = asyncio.Semaphore(WARMUP_CONCURRENCY)

        async def _drop_key(exact_hash: str) -> bool:
            async with semaphore:
                try:
                    await router.redis_service.delete(exact_hash)
                    return True
                except Exception:
                    logger.exception(
                        f"Redis eviction failed for exact_hash {exact_hash}"
                    )
                    return False

        results = await asyncio.gather(
            *(_drop_key(h) for h in plan.get("exact_hashes", []))
        )
        redis_deleted = sum(1 for ok in results if ok)

    logger.info(
        f"Bulk cache eviction ({'dry run' if body.dry_run else 'applied'}) "
        f"matched={plan['matched']} deleted={plan['deleted']} "
        f"redis={redis_deleted} filters={applied_filters} "
        f"(admin key {identity.key_id})"
    )

    return BaseAPIResponse.success_response(
        CacheBulkEvictResponse(
            filters=applied_filters,
            matched=plan["matched"],
            deleted=plan["deleted"],
            redis_deleted=redis_deleted,
            dry_run=body.dry_run,
        )
    )
