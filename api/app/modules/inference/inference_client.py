"""HTTP client for the shared inference platform.

Implements the consumer side of `inference-platform/contract/INFERENCE_CONTRACT.md`.
Request-path calls are fail-open by design: the gateway must still answer from the
provider when the inference service is unreachable, so callers treat
`InferenceUnavailable` as "skip the cache layer", never as a request failure.
Startup is the exception -- `verify()` raises `InferenceConfigError` so a
misconfigured or missing engine stops the process instead of silently serving
with no cache at all.

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


class InferenceConfigError(InferenceError):
    """The engine cannot serve this gateway; the process must not start.

    Raised only from `verify()` at startup. A request-path failure is a
    `InferenceUnavailable` and degrades; a boot-time failure means every request
    would silently bypass the cache, so it is fatal instead.
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

    Supports per-role endpoint URLs (`embed_url`, `rerank_url`, `relevance_url`,
    `nli_url`) for production deployments where models are split across distinct
    microservices or containers. Each role URL falls back to `base_url` when unset,
    preserving single-container / local development with zero overhead.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        embed_url: str | None = None,
        rerank_url: str | None = None,
        relevance_url: str | None = None,
        nli_url: str | None = None,
    ) -> None:
        self.base_url = (base_url or config.INFERENCE_BASE_URL).rstrip("/")
        self.embed_url = (embed_url or config.INFERENCE_EMBED_URL).rstrip("/")
        self.rerank_url = (rerank_url or config.INFERENCE_RERANK_URL).rstrip("/")
        self.relevance_url = (relevance_url or config.INFERENCE_RELEVANCE_URL).rstrip(
            "/"
        )
        self.nli_url = (nli_url or config.INFERENCE_NLI_URL).rstrip("/")

        self.enabled = bool(
            self.base_url
            or self.embed_url
            or self.rerank_url
            or self.relevance_url
            or self.nli_url
        )
        self.timeout = config.INFERENCE_TIMEOUT_SECONDS
        self.max_retries = config.INFERENCE_MAX_RETRIES

        self._async_clients: dict[str, httpx.AsyncClient] = {}
        self._sync_clients: dict[str, httpx.Client] = {}

    def url_for(self, role: str) -> str:
        """Resolve the effective base URL for a given role.

        Per-role URL overrides take precedence; if unset, fall back to self.base_url.
        """
        if role == "embed":
            return self.embed_url or self.base_url
        if role == "rerank":
            return self.rerank_url or self.base_url
        if role == "relevance":
            return self.relevance_url or self.rerank_url or self.base_url
        if role in ("nli", "classify"):
            return self.nli_url or self.base_url
        return self.base_url

    def timeout_for(self, role: str) -> float:
        """Resolve the configured timeout for a given role."""
        if role == "embed":
            return config.INFERENCE_EMBED_TIMEOUT_SECONDS
        if role == "rerank":
            return config.INFERENCE_RERANK_TIMEOUT_SECONDS
        if role == "relevance":
            return config.INFERENCE_RELEVANCE_TIMEOUT_SECONDS
        if role in ("nli", "classify"):
            return config.INFERENCE_NLI_TIMEOUT_SECONDS
        return self.timeout

    # -- backward-compatible client accessors -------------------------------

    @property
    def _async_client(self) -> httpx.AsyncClient | None:
        target_url = self.base_url or (
            next(iter(self._async_clients)) if self._async_clients else ""
        )
        return self._async_clients.get(target_url)

    @_async_client.setter
    def _async_client(self, client: httpx.AsyncClient | None) -> None:
        if client is None:
            self._async_clients.clear()
        else:
            target_url = self.base_url or ""
            self._async_clients[target_url] = client

    @property
    def _sync_client(self) -> httpx.Client | None:
        target_url = self.base_url or (
            next(iter(self._sync_clients)) if self._sync_clients else ""
        )
        return self._sync_clients.get(target_url)

    @_sync_client.setter
    def _sync_client(self, client: httpx.Client | None) -> None:
        if client is None:
            self._sync_clients.clear()
        else:
            target_url = self.base_url or ""
            self._sync_clients[target_url] = client

    # -- lifecycle ---------------------------------------------------------

    def _limits(self) -> httpx.Limits:
        return httpx.Limits(max_keepalive_connections=20, max_connections=100)

    def _timeout_for_val(self, val: float) -> httpx.Timeout:
        return httpx.Timeout(val, connect=_CONNECT_TIMEOUT)

    def _async_for(
        self, url: str | None = None, timeout: float | None = None
    ) -> httpx.AsyncClient:
        target_url = url or self.base_url
        effective_timeout = timeout or self.timeout
        client = self._async_clients.get(target_url)
        if client is None and self.base_url in self._async_clients and not target_url:
            client = self._async_clients.get(self.base_url)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                base_url=target_url,
                timeout=self._timeout_for_val(effective_timeout),
                limits=self._limits(),
            )
            self._async_clients[target_url] = client
        return client

    def _sync_for(
        self, url: str | None = None, timeout: float | None = None
    ) -> httpx.Client:
        target_url = url or self.base_url
        effective_timeout = timeout or self.timeout
        client = self._sync_clients.get(target_url)
        if client is None and self.base_url in self._sync_clients and not target_url:
            client = self._sync_clients.get(self.base_url)
        if client is None or client.is_closed:
            client = httpx.Client(
                base_url=target_url,
                timeout=self._timeout_for_val(effective_timeout),
                limits=self._limits(),
            )
            self._sync_clients[target_url] = client
        return client

    def _async(self) -> httpx.AsyncClient:
        return self._async_for(self.base_url)

    def _sync(self) -> httpx.Client:
        return self._sync_for(self.base_url)

    async def aclose(self) -> None:
        for client in list(self._async_clients.values()):
            if not client.is_closed:
                await client.aclose()
        self._async_clients.clear()
        for sync_client in list(self._sync_clients.values()):
            if not sync_client.is_closed:
                sync_client.close()
        self._sync_clients.clear()

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

    def _transport_error(
        self, exc: Exception, operation: str, target_url: str = ""
    ) -> InferenceUnavailable:
        url = target_url or self.base_url
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return InferenceUnreachable(
                f"{operation} failed: cannot connect to {url} ({exc!r})"
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
        url = self.url_for("embed")
        timeout = self.timeout_for("embed")
        payload = self._embed_payload(texts, model)

        async def _call() -> list[list[float]]:
            try:
                response = await self._async_for(url, timeout).post(
                    "/embeddings", json=payload
                )
            except (httpx.HTTPError, OSError) as exc:
                raise self._transport_error(exc, "embeddings", url) from exc
            self._raise_for(response, "embeddings")
            return self._extract_vectors(response.json(), len(texts))

        return await execute_with_retry(
            _call,
            timeout_seconds=timeout,
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
        url = self.url_for("embed")
        timeout = self.timeout_for("embed")
        payload = self._embed_payload(texts, model)
        try:
            response = self._sync_for(url, timeout).post("/embeddings", json=payload)
        except (httpx.HTTPError, OSError) as exc:
            raise self._transport_error(exc, "embeddings", url) from exc
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
        url = self.url_for(role)
        timeout = self.timeout_for(role)
        payload = self._rerank_payload(query, documents, role, model)

        async def _call() -> list[float]:
            try:
                response = await self._async_for(url, timeout).post(
                    "/rerank", json=payload
                )
            except (httpx.HTTPError, OSError) as exc:
                raise self._transport_error(exc, "rerank", url) from exc
            self._raise_for(response, "rerank")
            return self._extract_scores(response.json(), len(documents))

        return await execute_with_retry(
            _call,
            timeout_seconds=timeout,
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
        url = self.url_for(role)
        timeout = self.timeout_for(role)
        payload = self._rerank_payload(query, documents, role, model)
        try:
            response = self._sync_for(url, timeout).post("/rerank", json=payload)
        except (httpx.HTTPError, OSError) as exc:
            raise self._transport_error(exc, "rerank", url) from exc
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
        url = self.url_for("nli")
        timeout = self.timeout_for("nli")
        payload = self._classify_payload(pairs, model)

        async def _call() -> list[list[float]]:
            try:
                response = await self._async_for(url, timeout).post(
                    "/classify", json=payload
                )
            except (httpx.HTTPError, OSError) as exc:
                raise self._transport_error(exc, "classify", url) from exc
            self._raise_for(response, "classify")
            return self._extract_logits(response.json(), len(pairs))

        return await execute_with_retry(
            _call,
            timeout_seconds=timeout,
            max_retries=self.max_retries,
            retry_predicate=_is_retryable,
        )

    def classify_sync(
        self, pairs: list[tuple[str, str]], *, model: str | None = None
    ) -> list[list[float]]:
        if not pairs:
            return []
        url = self.url_for("nli")
        timeout = self.timeout_for("nli")
        payload = self._classify_payload(pairs, model)
        try:
            response = self._sync_for(url, timeout).post("/classify", json=payload)
        except (httpx.HTTPError, OSError) as exc:
            raise self._transport_error(exc, "classify", url) from exc
        self._raise_for(response, "classify")
        return self._extract_logits(response.json(), len(pairs))

    # -- startup verification ----------------------------------------------

    def required_roles(self) -> list[str]:
        """Roles this gateway depends on, honouring the USE_* role toggles."""
        roles = ["embed"]
        if config.USE_RERANKER_MODEL:
            roles.append("rerank")
        if config.USE_RELEVANCE_MODEL:
            roles.append("relevance")
        if config.USE_NLI_MODEL:
            roles.append("nli")
        return roles

    def role_urls(self) -> dict[str, str]:
        """Resolve the endpoint that serves each role."""
        return {
            "embed": self.embed_url or self.base_url,
            "rerank": self.rerank_url or self.base_url,
            "relevance": self.relevance_url or self.rerank_url or self.base_url,
            "nli": self.nli_url or self.base_url,
        }

    async def verify(self) -> dict[str, Any]:
        """Fail startup unless the engine can serve every role this gateway needs.

        Nothing is loaded in-process any more, so a missing engine is not a
        "working but degraded" state: every request would bypass the cache while
        `/ready` stayed green, and the only symptom would be a hit rate that never
        rises. That is a silent regression rather than a transient fault, so this
        raises instead of logging.

        Checked here, all fatal:

        * an engine URL is configured at all;
        * every distinct configured endpoint is reachable;
        * each required role is reported as loaded by that role's endpoint, which
          catches an engine that started without one model;
        * the engine's embedding dim and model id match what the Qdrant collection
          was populated with -- a mismatch mixes two vector spaces in one index.

        `GET /models` is contract, not a requirement: an engine that answers 404/405
        (Infinity) is reachable but unverifiable, so those checks are skipped with a
        warning rather than blocking a valid deployment.
        """
        if not self.enabled:
            raise InferenceConfigError(
                "INFERENCE_BASE_URL is not set. Dense embeddings, reranking and NLI "
                "are no longer loaded in-process; start the inference engine and "
                "point INFERENCE_BASE_URL at it (see docs/DEVELOPMENT.md)."
            )

        role_urls = self.role_urls()
        bodies: dict[str, dict[str, Any]] = {}
        unverified: set[str] = set()
        for url in sorted({url for url in role_urls.values() if url}):
            try:
                response = await self._async_for(url).get("/models")
            except (httpx.HTTPError, OSError) as exc:
                raise InferenceConfigError(
                    f"inference: cannot reach {url} ({exc!r}). Start the inference "
                    "engine (inference-platform/dev/native_server.py) or fix "
                    "INFERENCE_BASE_URL."
                ) from exc
            if response.status_code in (404, 405):
                # Third-party engines (Infinity) implement the three inference
                # routes but not our introspection endpoint. Reachable is all we
                # can prove, so say so once instead of refusing to boot.
                unverified.add(url)
                logger.warning(
                    "inference: %s does not implement GET /models (HTTP %s); role and "
                    "dimension verification is skipped for it. A role that is not "
                    "loaded will surface as a degraded cache at request time.",
                    url,
                    response.status_code,
                )
                bodies[url] = {}
                continue
            if response.status_code >= 400:
                raise InferenceConfigError(
                    f"inference: {url}/models returned HTTP {response.status_code}"
                )
            bodies[url] = response.json() or {}

        missing = [
            role
            for role in self.required_roles()
            if role_urls[role] not in unverified
            and role not in ((bodies.get(role_urls[role]) or {}).get("loaded") or {})
        ]
        if missing:
            loaded: set[str] = set()
            failed: dict[str, Any] = {}
            for body in bodies.values():
                loaded.update(body.get("loaded") or {})
                failed.update(body.get("failed") or {})
            raise InferenceConfigError(
                f"inference: engine is missing required role(s) {sorted(missing)}; "
                f"loaded={sorted(loaded)} failed={failed}. Check "
                "inference-platform/models.lock.yaml and the engine log."
            )

        embed = ((bodies.get(role_urls["embed"]) or {}).get("loaded") or {}).get(
            "embed"
        ) or {}
        remote_dim = embed.get("dim")
        if remote_dim and int(remote_dim) != int(config.INFERENCE_EMBEDDING_DIM):
            raise InferenceConfigError(
                f"inference: engine serves dim={remote_dim} but the gateway expects "
                f"INFERENCE_EMBEDDING_DIM={config.INFERENCE_EMBEDDING_DIM}. Qdrant "
                "vectors would be mixed; fix the lock or create a new collection."
            )
        remote_model = str(embed.get("model") or "")
        if remote_model and config.EMBEDDING_MODEL_NAME:
            if config.EMBEDDING_MODEL_NAME not in remote_model:
                raise InferenceConfigError(
                    f"inference: engine serves {remote_model} but "
                    f"EMBEDDING_MODEL_NAME={config.EMBEDDING_MODEL_NAME}. Cached "
                    "points were written under a different model; bump "
                    "EMBEDDING_VERSION and migrate rather than mixing them."
                )

        logger.debug(
            "inference: verified %d role(s) across %d endpoint(s) "
            "(embed=%s rev=%s dim=%s device=%s)",
            len(self.required_roles()),
            len(bodies),
            remote_model,
            embed.get("revision"),
            remote_dim,
            next(iter(bodies.values())).get("device"),
        )
        return next(iter(bodies.values()))

    async def healthy(self) -> bool:
        """Non-raising `verify()`, for dependency alerting (`/health/deps`)."""
        try:
            await self.verify()
        except InferenceError:
            return False
        return True
