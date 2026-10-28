"""Wire-level tests for the inference contract.

Deliberately no real models: every role below is a stand-in, so the suite runs in
under a second and does not depend on a multi-gigabyte weight download. The point
of these tests is the HTTP contract (routing, fallback semantics, error shapes),
not the numerics of a specific checkpoint — `evals/check.py` covers those against
a live server.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from inference_platform.api import create_app
from inference_platform.roles import Registry, _Role

SPEC = {
    "version": 1,
    "roles": {
        "embed": {"model": "fake/dense", "kind": "embedder", "dim": 3},
        "rerank": {"model": "fake/rerank", "kind": "cross-encoder"},
        "nli": {
            "model": "fake/nli",
            "kind": "cross-encoder",
            "labels": ["contradiction", "entailment", "neutral"],
        },
        "sparse": {"model": "fake/bm25", "kind": "sparse"},
    },
}


class FakeRole(_Role):
    """A role that loads nothing.

    It reuses the real `_Role.__init__` (which only records metadata) so the shape
    under test — `describe()` included — stays the production one. Only the
    forward pass is faked.
    """

    def __init__(
        self,
        kind: str,
        model_id: str,
        *,
        dim: int | None = None,
        labels: list[str] | None = None,
    ) -> None:
        super().__init__(
            kind,
            {
                "model": model_id,
                "revision": "deadbeef" if kind != "sparse" else "",
                "dim": dim,
                "labels": labels or [],
            },
            "cpu",
        )
        # Real loaders declare `kind` as a class attribute; the fake is one class
        # for all three kinds, so it pins the instance attribute instead.
        self.kind = kind
        self.model = None


class FakeDense(FakeRole):
    def __init__(self) -> None:
        super().__init__("embedder", "fake/dense", dim=3)
        self.calls = 0

    def encode(self, texts: list[str], batch_size: int) -> np.ndarray:
        self.calls += 1
        return np.tile(np.array([0.1, 0.2, 0.3], dtype=np.float32), (len(texts), 1))


class FakeCrossEncoder(FakeRole):
    def __init__(self, labels: list[str] | None = None) -> None:
        super().__init__("cross-encoder", "fake/rerank", labels=labels)

    def predict(self, pairs: list[tuple[str, str]], batch_size: int) -> np.ndarray:
        # One logit per pair, increasing with the document length so ordering is
        # deterministic and visible in the assertions.
        raw = [float(len(hypothesis) - 5) for _, hypothesis in pairs]
        if self.labels:
            return np.array(
                [[-1.0, value, 0.0][: len(self.labels)] for value in raw],
                dtype=np.float32,
            )
        return np.array(raw, dtype=np.float32)


class FakeSparse(FakeRole):
    def __init__(self, fail: bool = False) -> None:
        super().__init__("sparse", "fake/bm25")
        self.fail = fail

    def encode_sparse(self, texts: list[str], batch_size: int) -> list[dict]:
        if self.fail:
            raise RuntimeError("onnx session died")
        return [
            {"indices": [1, 7, 42], "values": [0.5, 0.25, float(len(text))]}
            for text in texts
        ]


def default_roles(*, sparse_fail: bool = False) -> dict:
    return {
        "embed": FakeDense(),
        "rerank": FakeCrossEncoder(),
        "nli": FakeCrossEncoder(labels=["contradiction", "entailment", "neutral"]),
        "sparse": FakeSparse(fail=sparse_fail),
    }


def build_client(
    *,
    sparse_fail: bool = False,
    roles: dict | None = None,
    spec: dict | None = None,
) -> TestClient:
    registry = Registry()
    registry.spec = spec if spec is not None else SPEC
    registry.roles = (
        roles if roles is not None else default_roles(sparse_fail=sparse_fail)
    )
    return TestClient(create_app(registry, load_models=False))


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #


def test_health_reports_loaded_roles_and_kinds() -> None:
    with build_client() as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "1"
    assert body["roles"]["sparse"]["kind"] == "sparse"
    assert body["roles"]["sparse"]["backend"].startswith("fastembed ")
    assert body["roles"]["embed"]["dim"] == 3


def test_health_degrades_when_a_role_failed() -> None:
    registry = Registry()
    registry.spec = SPEC
    registry.roles = {"embed": FakeDense()}
    registry.failed = {"nli": "RuntimeError: download failed"}
    with TestClient(create_app(registry, load_models=False)) as client:
        response = client.get("/health")
    assert response.status_code == 503
    body = response.json()["detail"]
    assert body["status"] == "degraded"
    assert body["unloaded"] == {"nli": "RuntimeError: download failed"}


def test_models_lists_spec_and_loaded_kinds() -> None:
    with build_client() as client:
        body = client.get("/models").json()
    assert body["loaded"]["sparse"]["kind"] == "sparse"
    assert body["roles"]["nli"]["labels"] == [
        "contradiction",
        "entailment",
        "neutral",
    ]
    assert body["failed"] == {}


# --------------------------------------------------------------------------- #
# /embeddings
# --------------------------------------------------------------------------- #


def test_dense_only_is_unchanged_for_old_consumers() -> None:
    with build_client() as client:
        response = client.post(
            "/embeddings", json={"role": "embed", "input": ["a", "b"]}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert body["model"] == "fake/dense"
    assert [row["index"] for row in body["data"]] == [0, 1]
    assert body["data"][0]["embedding"] == pytest.approx([0.1, 0.2, 0.3])
    # No new keys appear when sparse was not requested.
    assert "sparse" not in body
    assert "sparse_model" not in body


def test_dense_and_sparse_come_back_from_one_request() -> None:
    with build_client() as client:
        response = client.post(
            "/embeddings",
            json={"input": ["first", "second"], "dense": True, "sparse": True},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "fake/dense"
    assert body["sparse_model"] == "fake/bm25"
    assert len(body["data"]) == 2
    assert body["sparse"] == [
        {"index": 0, "indices": [1, 7, 42], "values": [0.5, 0.25, 5.0]},
        {"index": 1, "indices": [1, 7, 42], "values": [0.5, 0.25, 6.0]},
    ]


def test_sparse_only_omits_dense_keys() -> None:
    with build_client() as client:
        response = client.post(
            "/embeddings", json={"input": "solo", "dense": False, "sparse": True}
        )
    assert response.status_code == 200
    body = response.json()
    assert "data" not in body
    assert "model" not in body
    assert body["sparse"][0]["values"][2] == 4.0
    assert body["usage"]["prompt_tokens"] == 1


def test_neither_dense_nor_sparse_is_rejected() -> None:
    with build_client() as client:
        response = client.post(
            "/embeddings", json={"input": ["x"], "dense": False, "sparse": False}
        )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "nothing_requested"


def test_sparse_failure_degrades_without_losing_dense() -> None:
    with build_client(sparse_fail=True) as client:
        response = client.post(
            "/embeddings", json={"input": ["x"], "dense": True, "sparse": True}
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 1
    assert "sparse" not in body
    assert "onnx session died" in body["sparse_error"]


def test_sparse_failure_alone_is_a_structured_500() -> None:
    with build_client(sparse_fail=True) as client:
        response = client.post(
            "/embeddings", json={"input": ["x"], "dense": False, "sparse": True}
        )
    assert response.status_code == 500
    assert response.json()["detail"]["error"] == "inference_failed"
    assert response.json()["detail"]["branch"] == "sparse"


def test_sparse_role_not_loaded_is_503() -> None:
    roles = default_roles()
    roles.pop("sparse")
    with build_client(roles=roles) as client:
        response = client.post("/embeddings", json={"input": ["x"], "sparse": True})
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "model_not_loaded"


def test_sparse_role_absent_from_lock_is_400() -> None:
    spec = {
        "roles": {
            name: value for name, value in SPEC["roles"].items() if name != "sparse"
        }
    }
    roles = default_roles()
    roles.pop("sparse")
    with build_client(roles=roles, spec=spec) as client:
        response = client.post("/embeddings", json={"input": ["x"], "sparse": True})
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "unknown_role"


def test_empty_input_returns_empty_arrays() -> None:
    with build_client() as client:
        response = client.post(
            "/embeddings", json={"input": [], "dense": True, "sparse": True}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["sparse"] == []


def test_dimension_mismatch_is_rejected() -> None:
    with build_client() as client:
        response = client.post("/embeddings", json={"input": ["x"], "dimensions": 768})
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "dimension_mismatch"


def test_unknown_model_is_a_caller_error() -> None:
    with build_client() as client:
        response = client.post("/embeddings", json={"input": ["x"], "model": "nope"})
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "unknown_model"


def test_embedding_route_rejects_a_sparse_role() -> None:
    with build_client() as client:
        response = client.post(
            "/embeddings", json={"input": ["x"], "dense": True, "role": "sparse"}
        )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "not_an_embedding_model"


# --------------------------------------------------------------------------- #
# /rerank and /classify
# --------------------------------------------------------------------------- #


def test_rerank_sorts_by_sigmoid_score_and_keeps_raw() -> None:
    with build_client() as client:
        body = client.post(
            "/rerank",
            json={
                "role": "rerank",
                "query": "how do I rotate a key",
                "documents": ["short", "a much longer candidate document"],
            },
        ).json()
    assert [row["index"] for row in body["results"]] == [1, 0]
    assert body["results"][0]["score_raw"] > body["results"][1]["score_raw"]
    assert 0.0 <= body["results"][0]["score"] <= 1.0
    assert body["meta"]["billed_units"]["documents"] == 2


def test_rerank_rejects_an_embedder() -> None:
    with build_client() as client:
        response = client.post(
            "/rerank", json={"role": "embed", "query": "q", "documents": ["d"]}
        )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "not_a_cross_encoder"


def test_classify_returns_normalized_label_probabilities() -> None:
    with build_client() as client:
        body = client.post(
            "/classify", json={"role": "nli", "pairs": [["question", "an answer"]]}
        ).json()
    assert body["labels"] == ["contradiction", "entailment", "neutral"]
    probs = body["results"][0]["probabilities"]
    assert sum(probs.values()) == pytest.approx(1.0)
    assert body["results"][0]["label"] in body["labels"]


def test_classify_rejects_a_role_without_labels() -> None:
    with build_client() as client:
        response = client.post(
            "/classify", json={"role": "rerank", "pairs": [["a", "b"]]}
        )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "no_labels_declared"
