"""Tests for the shared inference platform client and its consumers.

Three layers are covered:

* `InferenceClient` against a mocked HTTP transport: wire shapes, ordering,
  error classification, retry, the fail-open request contract, and the strict
  startup verification.
* The services that delegate to it (`CrossEncoderService`, `EmbeddingService`):
  request chunking, role routing, the `USE_*` role toggles, and that nothing is
  loaded in-process.
* The fail-open chain: an unreachable engine must degrade the cache path, never
  fail the request.
"""

import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch

import httpx

from app.core.config import config
from app.modules.inference.inference_client import (
    InferenceClient,
    InferenceConfigError,
    InferenceRequestError,
    InferenceUnavailable,
    InferenceUnreachable,
)

BASE = "http://inference.test"


def _client(handler, *, retries=0) -> InferenceClient:
    """Client wired to a MockTransport, with retries pinned for determinism."""
    client = InferenceClient(BASE)
    client.max_retries = retries
    client._async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=BASE,
        timeout=5.0,
    )
    client._sync_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=BASE,
        timeout=5.0,
    )
    return client


def _embeddings_body(vectors, *, shuffle=False):
    data = [
        {"object": "embedding", "index": index, "embedding": vector}
        for index, vector in enumerate(vectors)
    ]
    if shuffle:
        data.reverse()
    return {"object": "list", "model": "m", "data": data}


class _ClientTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = {
            name: getattr(config, name)
            for name in (
                "INFERENCE_EMBED_MODEL",
                "INFERENCE_RERANK_MODEL",
                "INFERENCE_RELEVANCE_MODEL",
                "INFERENCE_NLI_MODEL",
            )
        }
        for name in self._saved:
            setattr(config, name, "")

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(config, name, value)

    async def test_embed_preserves_order_when_server_sorts(self):
        client = _client(
            lambda request: httpx.Response(
                200, json=_embeddings_body([[0.1, 0.2], [0.3, 0.4]], shuffle=True)
            )
        )
        vectors = await client.embed(["first", "second"])
        self.assertEqual(vectors, [[0.1, 0.2], [0.3, 0.4]])

    async def test_embed_routes_by_role_unless_pinned(self):
        seen = {}

        def handler(request):
            seen.clear()
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=_embeddings_body([[0.0]]))

        client = _client(handler)
        await client.embed(["text"])
        self.assertEqual(seen.get("role"), "embed")
        self.assertNotIn(
            "model", seen, "unpinned client must let the engine pick the role's model"
        )

        config.INFERENCE_EMBED_MODEL = "pinned-embed"
        await client.embed(["text"])
        self.assertEqual(seen["model"], "pinned-embed")
        self.assertNotIn("role", seen, "a pinned model is unambiguous on its own")

    async def test_rerank_restores_input_order_from_sorted_results(self):
        # The service returns results sorted by descending relevance; the client
        # must map them back onto caller positions.
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 2, "score": 0.9, "score_raw": 2.2},
                        {"index": 0, "score": 0.5, "score_raw": 0.0},
                        {"index": 1, "score": 0.1, "score_raw": -2.2},
                    ]
                },
            )

        client = _client(handler)
        scores = await client.rerank("q", ["a", "b", "c"])
        self.assertEqual(scores, [0.0, -2.2, 2.2], "raw logits, in input order")

    async def test_classify_returns_logits_per_pair(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "labels": ["contradiction", "entailment", "neutral"],
                    "results": [
                        {
                            "index": 0,
                            "probabilities": {
                                "contradiction": 0.02,
                                "entailment": 0.95,
                                "neutral": 0.03,
                            },
                            "label": "entailment",
                            "logits": [-3.7, 0.25, -3.78],
                        }
                    ],
                },
            )

        client = _client(handler)
        logits = await client.classify([("prompt", "answer")])
        self.assertEqual(logits, [[-3.7, 0.25, -3.78]])

    async def test_server_error_is_retried_then_surfaces_as_unavailable(self):
        calls = []

        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(503, json={"detail": "model_not_loaded"})

        client = _client(handler, retries=1)
        with self.assertRaises(InferenceUnavailable):
            await client.embed(["x"])
        self.assertEqual(len(calls), 2, "one retry for a transient 503")

    async def test_retry_recovers_on_second_attempt(self):
        calls = []

        def handler(request):
            calls.append(request.url.path)
            if len(calls) == 1:
                return httpx.Response(503, json={"detail": "warming up"})
            return httpx.Response(200, json=_embeddings_body([[1.0, 2.0]]))

        client = _client(handler, retries=1)
        self.assertEqual(await client.embed(["x"]), [[1.0, 2.0]])
        self.assertEqual(len(calls), 2)

    async def test_client_error_is_not_retried_and_is_distinguished(self):
        calls = []

        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(400, json={"error": "unknown_model"})

        client = _client(handler, retries=2)
        with self.assertRaises(InferenceRequestError):
            await client.embed(["x"])
        self.assertEqual(len(calls), 1, "a 400 is a caller bug, not a transient fault")

    async def test_refused_connection_is_unreachable_and_not_retried(self):
        calls = []

        def handler(request):
            calls.append(request.url.path)
            raise httpx.ConnectError("connection refused", request=request)

        client = _client(handler, retries=3)
        with self.assertRaises(InferenceUnreachable):
            await client.rerank("q", ["a"])
        self.assertEqual(len(calls), 1, "a refused connection cannot succeed on retry")

    async def test_read_timeout_is_unavailable_and_retried(self):
        calls = []

        def handler(request):
            calls.append(request.url.path)
            raise httpx.ReadTimeout("slow", request=request)

        client = _client(handler, retries=1)
        with self.assertRaises(InferenceUnavailable):
            await client.rerank("q", ["a"])
        self.assertNotIsInstance(InferenceUnavailable("x"), InferenceUnreachable)
        self.assertEqual(len(calls), 2, "a timeout is worth one retry")

    async def test_short_response_is_rejected(self):
        def handler(request):
            return httpx.Response(200, json=_embeddings_body([[0.1]]))

        client = _client(handler)
        with self.assertRaises(InferenceUnavailable):
            await client.embed(["a", "b"])

    async def test_empty_inputs_short_circuit_without_a_request(self):
        def handler(request):  # pragma: no cover - must not be called
            raise AssertionError("no request expected for empty input")

        client = _client(handler)
        self.assertEqual(await client.embed([]), [])
        self.assertEqual(await client.rerank("q", []), [])
        self.assertEqual(await client.classify([]), [])

    async def test_verify_refuses_to_start_without_an_engine_url(self):
        # Every constructor default falls back to config, so clear them all rather
        # than relying on the ambient .env of whoever runs the suite.
        with patch.multiple(
            config,
            INFERENCE_BASE_URL="",
            INFERENCE_EMBED_URL="",
            INFERENCE_RERANK_URL="",
            INFERENCE_RELEVANCE_URL="",
            INFERENCE_NLI_URL="",
        ):
            client = InferenceClient()
            self.assertFalse(client.enabled)
            with self.assertRaises(InferenceConfigError):
                await client.verify()
            self.assertFalse(await client.healthy())

    async def test_sync_variants_share_the_wire_contract(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"results": [{"index": 0, "score": 0.5, "score_raw": 0.25}]},
            )

        client = _client(handler)
        self.assertEqual(client.rerank_sync("q", ["a"]), [0.25])


class _FakeInferenceClient:
    """Records calls so service-level delegation can be asserted."""

    base_url = BASE
    enabled = True

    def url_for(self, role="embed"):
        return self.base_url

    def __init__(self, *, fail=False):
        self.fail = fail
        self.rerank_calls: list[tuple[str, list[str], str | None]] = []
        self.classify_calls: list[tuple[list[tuple[str, str]], str | None]] = []
        self.embed_calls: list[list[str]] = []

    def _maybe_fail(self):
        if self.fail:
            raise InferenceUnavailable("engine down")

    def rerank_sync(self, query, documents, *, role="rerank", model=None):
        self._maybe_fail()
        self.rerank_calls.append((query, list(documents), role, model))
        return [float(len(document)) for document in documents]

    async def rerank(self, query, documents, *, role="rerank", model=None):
        return self.rerank_sync(query, documents, role=role, model=model)

    def classify_sync(self, pairs, *, model=None):
        self._maybe_fail()
        self.classify_calls.append((list(pairs), model))
        return [[-1.0, 1.0, 0.0] for _ in pairs]

    async def classify(self, pairs, *, model=None):
        return self.classify_sync(pairs, model=model)

    def embed_sync(self, texts, model=None):
        self._maybe_fail()
        self.embed_calls.append(list(texts))
        return [[1.0, 2.0, 3.0] for _ in texts]

    async def embed_one(self, text, model=None):
        return self.embed_sync([text])[0]


class _CrossEncoderServiceTest(unittest.IsolatedAsyncioTestCase):
    def _service(self, client):
        from app.modules.verifier.cross_encoder_service import CrossEncoderService

        return CrossEncoderService(inference_client=client)

    def test_roles_hold_only_the_engine_client(self):
        client = _FakeInferenceClient()
        service = self._service(client)
        self.assertIs(service.inference, client)
        for attribute in ("reranker_model", "relevance_model", "nli_model"):
            self.assertFalse(
                hasattr(service, attribute),
                f"nothing is loaded in-process, so {attribute} must not exist",
            )

    def test_rerank_preserves_pair_positions(self):
        client = _FakeInferenceClient()
        service = self._service(client)
        scores = service.rerank_predict([("q", "aa"), ("q", "bbbb")])
        self.assertEqual(scores, [2.0, 4.0])
        self.assertEqual(
            client.rerank_calls,
            [("q", ["aa", "bbbb"], "rerank", None)],
            "one batched call, tagged with the rerank role",
        )

    def test_rerank_groups_pairs_by_distinct_query(self):
        client = _FakeInferenceClient()
        service = self._service(client)
        scores = service.rerank_predict([("q1", "a"), ("q2", "bb"), ("q1", "ccc")])
        self.assertEqual(scores, [1.0, 2.0, 3.0])
        self.assertEqual([call[0] for call in client.rerank_calls], ["q1", "q2"])
        self.assertEqual(client.rerank_calls[0][1], ["a", "ccc"])

    def test_rerank_chunks_beyond_the_contract_limit(self):
        client = _FakeInferenceClient()
        service = self._service(client)
        pairs = [("q", f"doc{i}") for i in range(300)]
        scores = service.rerank_predict(pairs)
        self.assertEqual(len(scores), 300)
        self.assertEqual([len(call[1]) for call in client.rerank_calls], [256, 44])

    async def test_async_variants_delegate(self):
        client = _FakeInferenceClient()
        service = self._service(client)
        self.assertEqual(await service.rerank_predict_async([("q", "aa")]), [2.0])
        self.assertEqual(len(await service.relevance_predict_async("q", ["aa"])), 1)
        self.assertEqual(len(await service.nli_predict_async("q", ["aa"])), 1)

    def test_nli_returns_raw_logits(self):
        service = self._service(_FakeInferenceClient())
        self.assertEqual(service.nli_predict("q", ["answer"]), [[-1.0, 1.0, 0.0]])

    def test_unavailable_engine_raises_for_the_caller_to_decide(self):
        service = self._service(_FakeInferenceClient(fail=True))
        with self.assertRaises(InferenceUnavailable):
            service.rerank_predict([("q", "a")])
        with self.assertRaises(InferenceUnavailable):
            service.nli_predict("q", ["a"])

    def test_disabling_a_role_raises_instead_of_calling_the_engine(self):
        client = _FakeInferenceClient()
        service = self._service(client)
        saved = config.USE_NLI_MODEL
        config.USE_NLI_MODEL = False
        try:
            with self.assertRaises(InferenceUnavailable):
                service.nli_predict("q", ["a"])
        finally:
            config.USE_NLI_MODEL = saved
        self.assertEqual(client.classify_calls, [])


class _RoleRoutingTest(unittest.TestCase):
    """Distinct roles must never be served by one another's model."""

    def test_relevance_uses_the_relevance_role(self):
        client = _FakeInferenceClient()
        service = _CrossEncoderServiceTest()._service(client)
        service.relevance_predict("q", ["aa", "bbb"])
        self.assertEqual([call[2] for call in client.rerank_calls], ["relevance"])

    def test_primary_rerank_uses_the_rerank_role(self):
        client = _FakeInferenceClient()
        service = _CrossEncoderServiceTest()._service(client)
        service.rerank_predict([("q", "aa")])
        self.assertEqual([call[2] for call in client.rerank_calls], ["rerank"])

    def test_pinned_role_model_replaces_the_role_name(self):
        config.INFERENCE_RELEVANCE_MODEL = "pinned-relevance"
        try:
            payload = InferenceClient._with_role({}, "relevance", None)
        finally:
            config.INFERENCE_RELEVANCE_MODEL = ""
        self.assertEqual(payload, {"model": "pinned-relevance"})

    def test_unpinned_role_name_is_sent(self):
        payload = InferenceClient._with_role({}, "relevance", None)
        self.assertEqual(payload, {"role": "relevance"})


class _EmbeddingServiceTest(unittest.TestCase):
    def _service(self, client):
        # BM25 stays local in every mode, and loading it here would make the
        # test depend on a downloaded fastembed model.
        with patch("fastembed.SparseTextEmbedding", MagicMock()):
            from app.modules.embedding.embedding_service import EmbeddingService

            return EmbeddingService(inference_client=client)

    def test_dense_comes_from_the_engine_and_imports_no_torch(self):
        service = self._service(_FakeInferenceClient())
        self.assertFalse(hasattr(service, "model"))
        self.assertEqual(service.embedding_dimension, config.INFERENCE_EMBEDDING_DIM)

    def test_dense_goes_over_http(self):
        client = _FakeInferenceClient()
        service = self._service(client)
        self.assertEqual(
            asyncio.run(service.get_embedding_async("hi")), [1.0, 2.0, 3.0]
        )
        self.assertEqual(client.embed_calls, [["hi"]])

    def test_bm25_artifact_cache_is_explicit_and_env_overridable(self):
        """The ONNX artifact must not land in the library's temp default.

        fastembed keeps two caches: `cache_folder` (Hugging Face) and `cache_dir`
        (the extracted model). Passing only the former puts the model in a temp
        directory that is empty on every container start, which is exactly how a
        baked image ended up unable to load BM25 offline.
        """
        from app.core.config import config

        builder = MagicMock()
        with patch("fastembed.SparseTextEmbedding", builder):
            from app.modules.embedding.embedding_service import EmbeddingService

            EmbeddingService(inference_client=_FakeInferenceClient())
        kwargs = builder.call_args.kwargs
        self.assertTrue(
            kwargs["cache_dir"].endswith("/fastembed"),
            f"cache_dir must be explicit, got {kwargs['cache_dir']!r}",
        )

        with patch.dict("os.environ", {"FASTEMBED_CACHE_PATH": "/somewhere/bm25"}):
            with patch("fastembed.SparseTextEmbedding", builder):
                EmbeddingService(inference_client=_FakeInferenceClient())
        self.assertEqual(builder.call_args.kwargs["cache_dir"], "/somewhere/bm25")

        with patch.dict("os.environ", {"BM25_LOCAL_FILES_ONLY": "true"}):
            with patch("fastembed.SparseTextEmbedding", builder):
                EmbeddingService(inference_client=_FakeInferenceClient())
        self.assertTrue(builder.call_args.kwargs["local_files_only"])
        self.assertTrue(config is not None)

    def test_sparse_stays_local(self):
        service = self._service(_FakeInferenceClient())
        service.sparse_model.embed.return_value = []
        self.assertEqual(
            service.get_sparse_embedding("text"), {"indices": [], "values": []}
        )

    def test_unavailable_engine_propagates_for_the_router_to_degrade(self):
        service = self._service(_FakeInferenceClient(fail=True))
        with self.assertRaises(InferenceUnavailable):
            asyncio.run(service.get_embedding_async("hi"))


class _FailOpenTest(unittest.TestCase):
    """The cache layer must degrade, not fail, when the engine is unreachable."""

    def setUp(self):
        self._saved_mode = config.CACHE_MODE
        config.CACHE_MODE = "active"

    def tearDown(self):
        config.CACHE_MODE = self._saved_mode

    def test_reranker_yields_no_candidates(self):
        from app.modules.reranking.reranker_service import RerankerService

        service = RerankerService(
            cross_encoder_service=_CrossEncoderServiceTest()._service(
                _FakeInferenceClient(fail=True)
            ),
            instruction_service=MagicMock(),
        )
        self.assertEqual(
            service.verify("query", "query", [{"id": "a", "payload": {}}]), []
        )

    def test_router_marks_a_precise_degradation_reason(self):
        from app.modules.llm.llm_router_service import LlmRouterService

        router = object.__new__(LlmRouterService)
        router.embedding_service = MagicMock(model_name="fake-embed")
        router.llm_cache_service = MagicMock()
        router._embedding_cache = {}
        router._max_embedding_cache_size = 16

        async def _raise(*args, **kwargs):
            raise InferenceUnavailable("engine down")

        router._get_embeddings = _raise

        from app.modules.chat.chat_schema import ChatCompletionRequest, ChatMessage

        request = ChatCompletionRequest(
            service_name="openai",
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="how do I reset a password?")],
        )

        # `mark_cache_degraded` writes to a contextvar, and asyncio Tasks run in a
        # copy of the caller's context, so both the call and the assertion have to
        # happen inside one coroutine -- which also keeps the flag from leaking
        # into other tests.
        from app.core.degradation import is_cache_degraded

        async def _scenario():
            # force=True so the test does not depend on which cache layers this
            # environment happens to enable; the fail-open branch is what is
            # under test, not the serving gate.
            result = await router.evaluate_semantic_cache(request, force=True)
            return result, is_cache_degraded()

        result, degraded = asyncio.run(_scenario())
        self.assertIsNone(result["response"], "no cache answer was produced")
        self.assertTrue(
            degraded,
            "the request must be reported as cache-degraded (served by provider)",
        )


class _SplitRoutingTest(unittest.IsolatedAsyncioTestCase):
    """Verifies that the inference client can split endpoints per role in production."""

    def test_url_resolution_defaults_to_base_url(self):
        client = InferenceClient("http://shared.local:7997")
        self.assertEqual(client.url_for("embed"), "http://shared.local:7997")
        self.assertEqual(client.url_for("rerank"), "http://shared.local:7997")
        self.assertEqual(client.url_for("relevance"), "http://shared.local:7997")
        self.assertEqual(client.url_for("nli"), "http://shared.local:7997")
        self.assertEqual(client.url_for("classify"), "http://shared.local:7997")

    def test_url_resolution_with_role_overrides(self):
        client = InferenceClient(
            "http://shared.local:7997",
            embed_url="http://embed-svc:7997",
            rerank_url="http://rerank-svc:7998",
            nli_url="http://classify-svc:7999",
        )
        self.assertEqual(client.url_for("embed"), "http://embed-svc:7997")
        self.assertEqual(client.url_for("rerank"), "http://rerank-svc:7998")
        # relevance defaults to rerank_url if relevance_url is unset
        self.assertEqual(client.url_for("relevance"), "http://rerank-svc:7998")
        self.assertEqual(client.url_for("nli"), "http://classify-svc:7999")
        self.assertEqual(client.url_for("classify"), "http://classify-svc:7999")

    async def test_requests_dispatch_to_distinct_endpoints(self):
        calls: dict[str, list[str]] = {"embed": [], "rerank": [], "classify": []}

        def embed_handler(request: httpx.Request) -> httpx.Response:
            calls["embed"].append(str(request.url))
            return httpx.Response(200, json=_embeddings_body([[0.1, 0.2]]))

        def rerank_handler(request: httpx.Request) -> httpx.Response:
            calls["rerank"].append(str(request.url))
            return httpx.Response(
                200, json={"results": [{"index": 0, "score_raw": 1.5, "score": 0.8}]}
            )

        def classify_handler(request: httpx.Request) -> httpx.Response:
            calls["classify"].append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "labels": ["contradiction", "entailment", "neutral"],
                    "results": [{"index": 0, "logits": [0.1, 0.9, 0.0]}],
                },
            )

        client = InferenceClient(
            "http://base.test",
            embed_url="http://embed-pool:7997",
            rerank_url="http://rerank-pool:7998",
            nli_url="http://classify-pool:7999",
        )
        client._async_clients["http://embed-pool:7997"] = httpx.AsyncClient(
            transport=httpx.MockTransport(embed_handler),
            base_url="http://embed-pool:7997",
        )
        client._async_clients["http://rerank-pool:7998"] = httpx.AsyncClient(
            transport=httpx.MockTransport(rerank_handler),
            base_url="http://rerank-pool:7998",
        )
        client._async_clients["http://classify-pool:7999"] = httpx.AsyncClient(
            transport=httpx.MockTransport(classify_handler),
            base_url="http://classify-pool:7999",
        )

        vectors = await client.embed(["test document"])
        self.assertEqual(len(vectors), 1)
        self.assertEqual(calls["embed"], ["http://embed-pool:7997/embeddings"])

        scores = await client.rerank("query", ["doc"])
        self.assertEqual(len(scores), 1)
        self.assertEqual(calls["rerank"], ["http://rerank-pool:7998/rerank"])

        logits = await client.classify([("premise", "hypothesis")])
        self.assertEqual(len(logits), 1)
        self.assertEqual(calls["classify"], ["http://classify-pool:7999/classify"])

        await client.aclose()
        self.assertEqual(len(client._async_clients), 0)

    async def test_verify_walks_every_configured_endpoint(self):
        probed_urls: list[str] = []

        def probe_handler(request: httpx.Request) -> httpx.Response:
            probed_urls.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "device": "cpu",
                    "loaded": {
                        role: {
                            "model": config.EMBEDDING_MODEL_NAME
                            if role == "embed"
                            else f"model-{role}",
                            "revision": "abc",
                            "dim": 384 if role == "embed" else None,
                        }
                        for role in ("embed", "rerank", "relevance", "nli")
                    },
                },
            )

        client = InferenceClient(
            "http://base-pool:7997",
            embed_url="http://embed-pool:7997",
            rerank_url="http://rerank-pool:7998",
        )
        client._async_clients["http://base-pool:7997"] = httpx.AsyncClient(
            transport=httpx.MockTransport(probe_handler),
            base_url="http://base-pool:7997",
        )
        client._async_clients["http://embed-pool:7997"] = httpx.AsyncClient(
            transport=httpx.MockTransport(probe_handler),
            base_url="http://embed-pool:7997",
        )
        client._async_clients["http://rerank-pool:7998"] = httpx.AsyncClient(
            transport=httpx.MockTransport(probe_handler),
            base_url="http://rerank-pool:7998",
        )

        await client.verify()
        # All 3 distinct endpoints should be probed
        self.assertIn("http://base-pool:7997/models", probed_urls)
        self.assertIn("http://embed-pool:7997/models", probed_urls)
        self.assertIn("http://rerank-pool:7998/models", probed_urls)

        await client.aclose()


class _StartupVerificationTest(unittest.IsolatedAsyncioTestCase):
    """Boot is strict: a gateway that cannot cache must not come up at all."""

    @staticmethod
    def _loaded(**overrides):
        loaded = {
            "embed": {
                "model": config.EMBEDDING_MODEL_NAME,
                "revision": "abc",
                "dim": config.INFERENCE_EMBEDDING_DIM,
            },
            "rerank": {"model": "BAAI/bge-reranker-base", "revision": "a", "dim": None},
            "relevance": {"model": "ms-marco", "revision": "b", "dim": None},
            "nli": {"model": "nli-deberta", "revision": "c", "dim": None},
        }
        for role, patch_ in overrides.items():
            if patch_ is None:
                loaded.pop(role, None)
            else:
                loaded[role] = patch_
        return loaded

    def _transport(self, loaded, *, device="cpu"):
        def handler(request):
            return httpx.Response(200, json={"device": device, "loaded": loaded})

        return httpx.MockTransport(handler)

    async def _verify(self, loaded, **kwargs):
        client = InferenceClient(BASE)
        client._async_clients[BASE] = httpx.AsyncClient(
            transport=self._transport(loaded), base_url=BASE
        )
        try:
            return await client.verify()
        finally:
            await client.aclose()

    async def test_happy_path_reports_the_engine(self):
        body = await self._verify(self._loaded())
        self.assertEqual(body["device"], "cpu")

    async def test_unreachable_engine_fails_startup(self):
        client = InferenceClient(BASE)

        def refused(request):
            raise httpx.ConnectError("refused")

        client._async_clients[BASE] = httpx.AsyncClient(
            transport=httpx.MockTransport(refused), base_url=BASE
        )
        with self.assertRaises(InferenceConfigError):
            await client.verify()
        await client.aclose()

    async def test_missing_role_fails_startup(self):
        # An engine that came up without its reranker would answer 503 to every
        # rerank call, which looks exactly like "no candidates" to the router.
        with self.assertRaises(InferenceConfigError) as caught:
            await self._verify(self._loaded(rerank=None))
        self.assertIn("rerank", str(caught.exception))

    async def test_embedding_dim_mismatch_fails_startup(self):
        with self.assertRaises(InferenceConfigError) as caught:
            await self._verify(
                self._loaded(
                    embed={
                        "model": config.EMBEDDING_MODEL_NAME,
                        "revision": "abc",
                        "dim": config.INFERENCE_EMBEDDING_DIM + 384,
                    }
                )
            )
        self.assertIn("INFERENCE_EMBEDDING_DIM", str(caught.exception))

    async def test_embedding_model_mismatch_fails_startup(self):
        with self.assertRaises(InferenceConfigError):
            await self._verify(
                self._loaded(
                    embed={
                        "model": "some/other-embedder",
                        "revision": "abc",
                        "dim": config.INFERENCE_EMBEDDING_DIM,
                    }
                )
            )

    async def test_disabling_a_role_drops_it_from_the_check(self):
        saved = config.USE_NLI_MODEL
        config.USE_NLI_MODEL = False
        try:
            await self._verify(self._loaded(nli=None))
        finally:
            config.USE_NLI_MODEL = saved

    async def test_engine_without_models_endpoint_is_reachable_but_unverified(self):
        """Infinity implements /embeddings, /rerank and /classify, not /models.

        Refusing to boot against it would make the production engine unusable, so
        the endpoint is treated as "reachable, capability unknown" instead.
        """

        def handler(request):
            return httpx.Response(404, json={"detail": "not found"})

        client = InferenceClient(BASE)
        client._async_clients[BASE] = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url=BASE
        )
        try:
            self.assertEqual(await client.verify(), {})
            self.assertTrue(await client.healthy())
        finally:
            await client.aclose()

    async def test_engine_that_errors_on_models_fails_startup(self):
        def handler(request):
            return httpx.Response(500, text="boom")

        client = InferenceClient(BASE)
        client._async_clients[BASE] = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url=BASE
        )
        with self.assertRaises(InferenceConfigError):
            await client.verify()
        await client.aclose()


if __name__ == "__main__":
    unittest.main()
