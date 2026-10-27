"""HTTP client for the shared inference platform.

Implements the consumer side of `inference-platform/contract/INFERENCE_CONTRACT.md`.
Everything here is fail-open by design: the gateway must still answer from the
provider when the inference service is unreachable, so callers treat
`InferenceUnavailable` as "skip the cache layer", never as a request failure.

Score scales are preserved deliberately. The contract exposes normalized
`score`/`probabilities` *and* `score_raw`/`logits`; this client returns the raw
values because every existing threshold in the gateway was calibrated against
raw cross-encoder logits. Adopting the normalized scale is a separate, measured
change (see `docs/ARCHITECTURE.md`, shadow cache mode).
"""

from __future__ import annotations

import logging
import math
from typing import Any

import httpx

from app.core.config import config
from app.core.retry import execute_with_retry

logger = logging.getLogger(__name__)

# Reasonable for a local sidecar: fast to fail, and the caller degrades to the
# provider path rather than hanging the request.
_CONNECT_TIMEOUT = 2.0


class InferenceError(RuntimeError):
    """Base class for inference-platform failures."""


class InferenceUnavailable(InferenceError):
    """The engine could not answer: transport failure, timeout, 5xx, or 503.

    Callers must degrade (skip semantic cache / use judge fallbacks) instead of
    raising to the client.
    """


class InferenceUnreachable(InferenceUnavailable):
    """No TCP connection could be made: the engine is down or not deployed.

    Distinguished from other `InferenceUnavailable` failures because retrying a
    refused connection cannot succeed and would only add backoff latency to a
    cache lookup that is about to fall through to the provider anyway.
    """


class InferenceRequestError(InferenceError):
    """The engine rejected the request (4xx other than 429).

    This is a caller bug -- a wrong model id or a payload the contract does not
    allow -- so it is logged loudly rather than silently swallowed.
    """


def _is_retryable(exc: BaseException) -> bool:
    """Transient faults only: 5xx, throttling, timeouts.

    A refused/failed connection is not transient -- the engine is down -- so it
    fails fast instead of spending the request's latency budget on backoff.
    """
    return isinstance(exc, InferenceUnavailable) and not isinstance(
        exc, InferenceUnreachable
    )


class InferenceClient:
    """Async client for embeddings, reranking, and NLI.

    Sync variants exist for the two places that legitimately run off the event
    loop (the reranker pipeline, which is dispatched with `asyncio.to_thread`,
    and CLI scripts). Mixing them is fine; calling a sync variant from a
    coroutine is not, because it blocks the loop for the full round trip.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or config.INFERENCE_BASE_URL).rstrip("/")
        self.enabled = bool(self.base_url)
        self.timeout = config.INFERENCE_TIMEOUT_SECONDS
        self.max_retries = config.INFERENCE_MAX_RETRIES
        self._async_client: httpx.AsyncClient | None = None
        self._sync_client: httpx.Client | None = None

    # -- lifecycle ---------------------------------------------------------

    def _limits(self) -> httpx.Limits:
        return httpx.Limits(max_keepalive_connections=20, max_connections=100)

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.timeout, connect=_CONNECT_TIMEOUT)

    def _async(self) -> httpx.AsyncClient:
        # Created lazily so the client binds to the loop that actually uses it.
        # The API process and each Celery prefork child own separate loops.
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout(),
                limits=self._limits(),
            )
        return self._async_client

    def _sync(self) -> httpx.Client:
        if self._sync_client is None or self._sync_client.is_closed:
            self._sync_client = httpx.Client(
                base_url=self.base_url,
                timeout=self._timeout(),
                limits=self._limits(),
            )
        return self._sync_client

    async def aclose(self) -> None:
        if self._async_client is not None and not self._async_client.is_closed:
            await self._async_client.aclose()
        self._async_client = None
        if self._sync_client is not None and not self._sync_client.is_closed:
            self._sync_client.close()
        self._sync_client = None

    # -- error mapping -----------------------------------------------------

    @staticmethod
    def _raise_for(response: httpx.Response, operation: str) -> None:
        if response.status_code < 400:
            return
        detail: Any
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:500]
        # 429 is a transient throttle; 503 means "model not loaded yet". Both are
        # worth retrying and both must degrade rather than fail the request.
        if response.status_code == 429 or response.status_code >= 500:
            raise InferenceUnavailable(
                f"{operation} -> HTTP {response.status_code}: {detail}"
            )
        raise InferenceRequestError(
            f"{operation} -> HTTP {response.status_code}: {detail}"
        )

    def _transport_error(self, exc: Exception, operation: str) -> InferenceUnavailable:
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return InferenceUnreachable(
                f"{operation} failed: cannot connect to {self.base_url} ({exc!r})"
            )
        return InferenceUnavailable(f"{operation} failed: {exc!r}")

    # -- embeddings --------------------------------------------------------

    @staticmethod
    def _pin_for(role: str) -> str:
        return {
            "embed": config.INFERENCE_EMBED_MODEL,
            "rerank": config.INFERENCE_RERANK_MODEL,
            "relevance": config.INFERENCE_RELEVANCE_MODEL,
            "nli": config.INFERENCE_NLI_MODEL,
        }.get(role, "")

    @classmethod
    def _with_role(
        cls, payload: dict[str, Any], role: str, model: str | None
    ) -> dict[str, Any]:
        """Name the role, and pin the model only when one is configured.

        Sending the role is what keeps roles from collapsing into each other: an
        unpinned `/rerank` call is otherwise served by the engine's *default*
        reranker, so `relevance_predict` would silently score against the wrong
        model. That failure was observed end to end (relevance scores differed by
        11.4) before role routing was added; see
        `tests/test_inference_client.py::_RoleRoutingTest`.

        The pin stays optional because engines that route by model id only
        (Infinity) require one, while role routing is the native backend's
        fallback for laptop development.
        """
        pin = model or cls._pin_for(role)
        if pin:
            payload["model"] = pin
        else:
            payload["role"] = role
        return payload

    def _embed_payload(self, texts: list[str], model: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"input": texts}
        return self._with_role(payload, "embed", model)

    @staticmethod
    def _extract_vectors(body: dict[str, Any], expected: int) -> list[list[float]]:
        data = body.get("data")
        if not isinstance(data, list) or len(data) != expected:
            raise InferenceUnavailable(
                f"embeddings response had {len(data or [])} vectors, expected {expected}"
            )
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        return [[float(v) for v in item["embedding"]] for item in ordered]

    async def embed(
        self, texts: list[str], *, model: str | None = None
    ) -> list[list[float]]:
        if not texts:
            return []
        payload = self._embed_payload(texts, model)

        async def _call() -> list[list[float]]:
            try:
                response = await self._async().post("/embeddings", json=payload)
            except (httpx.HTTPError, OSError) as exc:
                raise self._transport_error(exc, "embeddings") from exc
            self._raise_for(response, "embeddings")
            return self._extract_vectors(response.json(), len(texts))

        return await execute_with_retry(
            _call,
            timeout_seconds=self.timeout,
            max_retries=self.max_retries,
            retry_predicate=_is_retryable,
        )

    async def embed_one(self, text: str, *, model: str | None = None) -> list[float]:
        vectors = await self.embed([text], model=model)
        return vectors[0]

    def embed_sync(
        self, texts: list[str], *, model: str | None = None
    ) -> list[list[float]]:
        if not texts:
            return []
        payload = self._embed_payload(texts, model)
        try:
            response = self._sync().post("/embeddings", json=payload)
        except (httpx.HTTPError, OSError) as exc:
            raise self._transport_error(exc, "embeddings") from exc
        self._raise_for(response, "embeddings")
        return self._extract_vectors(response.json(), len(texts))

    # -- rerank ------------------------------------------------------------

    @staticmethod
    def _extract_scores(body: dict[str, Any], expected: int) -> list[float]:
        """Raw logits in *input* order.

        The service returns results sorted by descending relevance, each tagged
        with its original index; existing thresholds compare candidates by
        position, so the order has to be restored here.
        """
        results = body.get("results")
        if not isinstance(results, list) or len(results) != expected:
            raise InferenceUnavailable(
                f"rerank response had {len(results or [])} results, expected {expected}"
            )
        scores = [0.0] * expected
        for item in results:
            index = int(item["index"])
            if not 0 <= index < expected:
                raise InferenceUnavailable(f"rerank index {index} out of range")
            scores[index] = float(item.get("score_raw", 0.0))
        return scores

    def _rerank_payload(
        self,
        query: str,
        documents: list[str],
        role: str,
        model: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query, "documents": documents}
        return self._with_role(payload, role, model)

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        role: str = "rerank",
        model: str | None = None,
    ) -> list[float]:
        if not documents:
            return []
        payload = self._rerank_payload(query, documents, role, model)

        async def _call() -> list[float]:
            try:
                response = await self._async().post("/rerank", json=payload)
            except (httpx.HTTPError, OSError) as exc:
                raise self._transport_error(exc, "rerank") from exc
            self._raise_for(response, "rerank")
            return self._extract_scores(response.json(), len(documents))

        return await execute_with_retry(
            _call,
            timeout_seconds=self.timeout,
            max_retries=self.max_retries,
            retry_predicate=_is_retryable,
        )

    def rerank_sync(
        self,
        query: str,
        documents: list[str],
        *,
        role: str = "rerank",
        model: str | None = None,
    ) -> list[float]:
        if not documents:
            return []
        payload = self._rerank_payload(query, documents, role, model)
        try:
            response = self._sync().post("/rerank", json=payload)
        except (httpx.HTTPError, OSError) as exc:
            raise self._transport_error(exc, "rerank") from exc
        self._raise_for(response, "rerank")
        return self._extract_scores(response.json(), len(documents))

    # -- NLI ---------------------------------------------------------------

    @staticmethod
    def _logits_for(item: dict[str, Any], labels: list[str]) -> list[float]:
        logits = item.get("logits")
        if isinstance(logits, list) and logits:
            return [float(v) for v in logits[: len(labels)]]
        # Tolerate an implementation that only returns probabilities: log-space
        # keeps downstream softmax/argmax arithmetic working unchanged.
        probabilities = item.get("probabilities") or {}
        return [
            math.log(max(float(probabilities.get(label, 0.0)), 1e-12))
            for label in labels
        ]

    def _classify_payload(
        self, pairs: list[tuple[str, str]], model: str | None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pairs": [[premise, hypothesis] for premise, hypothesis in pairs]
        }
        return self._with_role(payload, "nli", model)

    @staticmethod
    def _extract_logits(body: dict[str, Any], expected: int) -> list[list[float]]:
        results = body.get("results")
        labels = body.get("labels") or []
        if not isinstance(results, list) or len(results) != expected:
            raise InferenceUnavailable(
                f"classify response had {len(results or [])} results, expected {expected}"
            )
        ordered = sorted(results, key=lambda item: item.get("index", 0))
        return [InferenceClient._logits_for(item, labels) for item in ordered]

    async def classify(
        self, pairs: list[tuple[str, str]], *, model: str | None = None
    ) -> list[list[float]]:
        if not pairs:
            return []
        payload = self._classify_payload(pairs, model)

        async def _call() -> list[list[float]]:
            try:
                response = await self._async().post("/classify", json=payload)
            except (httpx.HTTPError, OSError) as exc:
                raise self._transport_error(exc, "classify") from exc
            self._raise_for(response, "classify")
            return self._extract_logits(response.json(), len(pairs))

        return await execute_with_retry(
            _call,
            timeout_seconds=self.timeout,
            max_retries=self.max_retries,
            retry_predicate=_is_retryable,
        )

    def classify_sync(
        self, pairs: list[tuple[str, str]], *, model: str | None = None
    ) -> list[list[float]]:
        if not pairs:
            return []
        payload = self._classify_payload(pairs, model)
        try:
            response = self._sync().post("/classify", json=payload)
        except (httpx.HTTPError, OSError) as exc:
            raise self._transport_error(exc, "classify") from exc
        self._raise_for(response, "classify")
        return self._extract_logits(response.json(), len(pairs))

    # -- startup probe -----------------------------------------------------

    async def probe(self) -> dict[str, Any] | None:
        """Log the engine's effective models so version skew is visible.

        A dimension mismatch against the running Qdrant collection is a silent
        cache corruption, so it is worth one WARNING at startup.
        """
        if not self.enabled:
            return None
        try:
            response = await self._async().get("/models")
        except (httpx.HTTPError, OSError) as exc:
            logger.warning(
                "inference: cannot reach %s (%r); semantic caching will degrade "
                "to the provider path until it is available",
                self.base_url,
                exc,
            )
            return None

        if response.status_code >= 400:
            logger.warning("inference: /models returned HTTP %s", response.status_code)
            return None

        body = response.json()
        loaded = body.get("loaded") or {}
        embed = loaded.get("embed") or {}
        if embed:
            logger.info(
                "inference: serving %d role(s) from %s (device=%s, embed=%s rev=%s dim=%s)",
                len(loaded),
                self.base_url,
                body.get("device"),
                embed.get("model"),
                embed.get("revision"),
                embed.get("dim"),
            )
            remote_dim = embed.get("dim")
            expected_dim = config.INFERENCE_EMBEDDING_DIM
            if remote_dim and expected_dim and int(remote_dim) != int(expected_dim):
                logger.warning(
                    "inference: embedding dim mismatch -- engine serves %s but the "
                    "gateway expects %s (INFERENCE_EMBEDDING_DIM). Qdrant vectors "
                    "will not match; fix before trusting cache hits.",
                    remote_dim,
                    expected_dim,
                )
            remote_model = str(embed.get("model") or "")
            if remote_model and config.EMBEDDING_MODEL_NAME:
                if config.EMBEDDING_MODEL_NAME not in remote_model:
                    logger.warning(
                        "inference: engine serves %s but EMBEDDING_MODEL_NAME=%s; "
                        "existing vectors were written under a different model",
                        remote_model,
                        config.EMBEDDING_MODEL_NAME,
                    )
        return body
