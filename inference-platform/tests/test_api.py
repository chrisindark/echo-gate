"""Wire-level tests for the inference contract.

Deliberately no real models: every role below is a stand-in, so the suite runs in
under a second and does not depend on a multi-gigabyte weight download. The point
of these tests is the HTTP contract (routing, fallback semantics, error shapes),
not the numerics of a specific checkpoint — `evals/check.py` covers those against
a live server.
"""

from __future__ import annotations

import re

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
        "zero-shot": {
            "model": "fake/zero-shot",
            "kind": "cross-encoder",
            "labels": ["entailment", "not_entailment"],
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


class FakeNLI(FakeRole):
    """Entailment is high when the hypothesis names a word from the premise.

    That is what a working zero-shot setup looks like from the outside: the
    hypothesis `This text is about invoice.` entails a premise that talks about an
    invoice. It makes the ranking assertions meaningful instead of relying on
    hypothesis length.
    """

    def __init__(
        self, labels: list[str] | None = None, model_id: str = "fake/nli"
    ) -> None:
        super().__init__(
            "cross-encoder",
            model_id,
            labels=labels or ["contradiction", "entailment", "neutral"],
        )

    def predict(self, pairs: list[tuple[str, str]], batch_size: int) -> np.ndarray:
        # Logits are emitted in the role's *declared* label order, the way a real
        # checkpoint's `id2label` defines them: a 2-label zero-shot model puts
        # entailment at index 0, a 3-way NLI model puts it at index 1.
        base = {"contradiction": -2.0, "neutral": 0.0}
        rows = []
        for premise, hypothesis in pairs:
            premised = {word.strip(".,!?").lower() for word in premise.split()}
            matches = any(
                len(word) > 3 and word.lower() in premised
                for word in re.findall(r"[A-Za-z]+", hypothesis)
            )
            rows.append(
                [
                    (3.0 if matches else -2.0)
                    if label == "entailment"
                    else base.get(label, -2.0)
                    for label in self.labels
                ]
            )
        return np.array(rows, dtype=np.float32)


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
        "nli": FakeNLI(),
        # A dedicated zero-shot checkpoint is binary: entailment vs not. That is
        # the shape the real lock declares, so the fake matches it.
        "zero-shot": FakeNLI(
            labels=["entailment", "not_entailment"], model_id="fake/zero-shot"
        ),
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
    # `Registry.load` names each role after its lock key; the fakes are built
    # directly, so mirror that or `role.name` in a response would be meaningless.
    for name, role in registry.roles.items():
        role.name = name
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
    assert body["mode"] == "pairs"
    assert body["labels"] == ["contradiction", "entailment", "neutral"]
    probs = body["results"][0]["probabilities"]
    assert sum(probs.values()) == pytest.approx(1.0)
    assert body["results"][0]["label"] in body["labels"]


def test_classify_names_the_answering_role_and_model() -> None:
    """Consumers need to know which model answered, not just what it said."""
    with build_client() as client:
        body = client.post(
            "/classify", json={"role": "nli", "pairs": [["question", "an answer"]]}
        ).json()
    assert body["role"] == "nli"
    assert body["model"] == "fake/nli"


def test_classify_pairs_mode_requires_pairs() -> None:
    with build_client() as client:
        response = client.post("/classify", json={"role": "nli", "inputs": ["x"]})
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "missing_pairs"


def test_classify_rejects_a_role_without_labels() -> None:
    with build_client() as client:
        response = client.post(
            "/classify", json={"role": "rerank", "pairs": [["a", "b"]]}
        )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "no_labels_declared"


# --------------------------------------------------------------------------- #
# /classify?mode=zero-shot
# --------------------------------------------------------------------------- #

ZERO_SHOT = {
    "mode": "zero-shot",
    "inputs": ["my invoice was charged twice", "the weather is mild today"],
    "labels": ["invoice", "weather"],
}


def test_zero_shot_ranks_labels_without_training_anything() -> None:
    with build_client() as client:
        body = client.post("/classify", json=ZERO_SHOT).json()
    assert body["mode"] == "zero-shot"
    assert body["entailment_label"] == "entailment"
    assert body["labels"] == ["invoice", "weather"]
    assert body["hypothesis_template"] == "This text is about {}."

    first, second = body["results"]
    assert first["index"] == 0
    assert first["label"] == "invoice"
    assert first["score"] == pytest.approx(first["ranked"][0]["score"])
    assert first["scores"]["invoice"] > first["scores"]["weather"]
    assert second["label"] == "weather"
    assert second["scores"]["weather"] > second["scores"]["invoice"]


def test_zero_shot_uses_the_dedicated_role_when_the_lock_declares_one() -> None:
    with build_client() as client:
        body = client.post("/classify", json=ZERO_SHOT).json()
    assert body["role"] == "zero-shot"
    assert body["model"] == "fake/zero-shot"
    # Binary model: the score is P(entailment) directly, with no neutral class
    # diluting it.
    assert body["entailment_label"] == "entailment"


def test_zero_shot_falls_back_to_the_nli_role_on_engines_that_lack_it() -> None:
    spec = {
        "version": 1,
        "roles": {k: v for k, v in SPEC["roles"].items() if k != "zero-shot"},
    }
    roles = {k: v for k, v in default_roles().items() if k != "zero-shot"}
    with build_client(roles=roles, spec=spec) as client:
        body = client.post("/classify", json=ZERO_SHOT).json()
    assert body["role"] == "nli"
    assert body["model"] == "fake/nli"


def test_zero_shot_normalized_scores_are_a_distribution_over_labels() -> None:
    with build_client() as client:
        body = client.post("/classify", json=ZERO_SHOT).json()
    for row in body["results"]:
        assert sum(row["scores_normalized"].values()) == pytest.approx(1.0)
        assert row["scores_normalized"][row["label"]] == pytest.approx(
            max(row["scores_normalized"].values())
        )


def test_zero_shot_scores_are_independent_not_a_distribution() -> None:
    """Two labels that both fit the input each score high.

    That is the contract's point: every label is its own NLI question, so scores
    can sum above 1 and must not be read as a distribution. `scores_normalized`
    exists for consumers that must pick exactly one.
    """
    payload = {
        "mode": "zero-shot",
        "inputs": ["my invoice was charged twice"],
        "labels": ["invoice", "charged"],
    }
    with build_client() as client:
        row = client.post("/classify", json=payload).json()["results"][0]
    assert row["scores"]["invoice"] > 0.5
    assert row["scores"]["charged"] > 0.5
    assert sum(row["scores"].values()) > 1.0
    assert sum(row["scores_normalized"].values()) == pytest.approx(1.0)


def test_zero_shot_accepts_a_custom_hypothesis_template() -> None:
    payload = {**ZERO_SHOT, "hypothesis_template": "This email is about {}."}
    with build_client() as client:
        body = client.post("/classify", json=payload).json()
    assert body["hypothesis_template"] == "This email is about {}."


def test_zero_shot_requires_the_placeholder_in_the_template() -> None:
    payload = {**ZERO_SHOT, "hypothesis_template": "This email is about billing."}
    with build_client() as client:
        response = client.post("/classify", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "bad_hypothesis_template"


def test_zero_shot_rejects_duplicate_labels() -> None:
    payload = {**ZERO_SHOT, "labels": ["invoice", "invoice"]}
    with build_client() as client:
        response = client.post("/classify", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "duplicate_labels"


def test_zero_shot_requires_inputs_and_labels() -> None:
    with build_client() as client:
        no_inputs = client.post(
            "/classify", json={"mode": "zero-shot", "labels": ["invoice"]}
        )
        no_labels = client.post(
            "/classify", json={"mode": "zero-shot", "inputs": ["x"]}
        )
    assert no_inputs.status_code == 400
    assert no_inputs.json()["detail"]["error"] == "missing_inputs"
    assert no_labels.status_code == 400
    assert no_labels.json()["detail"]["error"] == "missing_labels"


def test_zero_shot_enforces_the_pair_budget() -> None:
    payload = {
        "mode": "zero-shot",
        "inputs": [f"document {i}" for i in range(20)],
        "labels": [f"label{i}" for i in range(20)],
    }
    with build_client() as client:
        response = client.post("/classify", json=payload)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "too_many_pairs"
    assert detail["got"] == 400


def test_zero_shot_needs_an_entailment_class() -> None:
    roles = default_roles()
    # Pinned to `nli` explicitly: the dedicated role is declared (and has an
    # entailment class), so an unpinned request would never reach this failure.
    roles["nli"] = FakeNLI(labels=["contradiction", "neutral"])
    with build_client(roles=roles) as client:
        response = client.post("/classify", json={**ZERO_SHOT, "role": "nli"})
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "no_entailment_label"


# --------------------------------------------------------------------------- #
# /classify?mode=zero-shot with verbatim `hypotheses`
# --------------------------------------------------------------------------- #

# Two labels over one premise. Only the second `phishing` phrasing shares a word
# with the premise, so a correct implementation must score the label by its best
# phrasing, not its first or last.
PREMISE = "your access was suspended after unusual activity"

CLAIM_HYPOTHESES = {
    "phishing": [
        "This message reports a routine account statement.",
        "This message pressures the reader to click a suspended link urgently.",
    ],
    "account notice": [
        "This message reports a routine account statement.",
    ],
}


def test_zero_shot_accepts_verbatim_hypotheses_keyed_by_label() -> None:
    payload = {
        "mode": "zero-shot",
        "inputs": [PREMISE],
        "hypotheses": CLAIM_HYPOTHESES,
    }
    with build_client() as client:
        body = client.post("/classify", json=payload).json()

    # `labels` may be omitted: the hypothesis keys are the label set.
    assert body["labels"] == ["phishing", "account notice"]
    assert body["hypotheses"] == CLAIM_HYPOTHESES
    # No template was applied, so echoing one would misdescribe the request.
    assert body["hypothesis_template"] is None
    assert body["entailment_label"] == "entailment"

    row = body["results"][0]
    assert row["label"] == "phishing"
    assert set(row["scores"]) == set(CLAIM_HYPOTHESES)
    # The phrasing that carried the label, so a caller can tell "wrong label"
    # from "phrasing never entailed".
    assert row["best_hypothesis"]["phishing"] == CLAIM_HYPOTHESES["phishing"][1]


def test_zero_shot_scores_a_label_by_its_best_hypothesis() -> None:
    payload = {"mode": "zero-shot", "inputs": [PREMISE], "hypotheses": CLAIM_HYPOTHESES}
    with build_client() as client:
        row = client.post("/classify", json=payload).json()["results"][0]
    # The winning phrasing scores high; the label whose only phrasing matches
    # nothing stays at the fake's indecision point (0.5, both classes tied).
    # Averaging the two `phishing` phrasings would erase the signal instead.
    assert row["scores"]["phishing"] > 0.9
    assert row["scores"]["account notice"] == pytest.approx(0.5)


def test_zero_shot_without_hypotheses_keeps_the_original_shape() -> None:
    """The template path must not grow fields it did not have."""
    with build_client() as client:
        body = client.post("/classify", json=ZERO_SHOT).json()
    assert "hypotheses" not in body
    assert body["hypothesis_template"] == "This text is about {}."
    assert "best_hypothesis" not in body["results"][0]


def test_zero_shot_rejects_hypotheses_and_a_template_together() -> None:
    payload = {
        "mode": "zero-shot",
        "inputs": [PREMISE],
        "hypotheses": CLAIM_HYPOTHESES,
        "hypothesis_template": "This email is about {}.",
    }
    with build_client() as client:
        response = client.post("/classify", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "conflicting_hypothesis_spec"


def test_zero_shot_rejects_labels_that_do_not_match_the_hypotheses() -> None:
    payload = {
        "mode": "zero-shot",
        "inputs": [PREMISE],
        "labels": ["phishing", "invoice"],
        "hypotheses": CLAIM_HYPOTHESES,
    }
    with build_client() as client:
        response = client.post("/classify", json=payload)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "hypotheses_label_mismatch"
    assert detail["hypotheses"] == ["phishing", "account notice"]


@pytest.mark.parametrize(
    ("texts", "error"),
    [
        ([], "empty_hypotheses"),
        (["  "], "empty_hypothesis"),
        (["same claim", "same claim"], "duplicate_hypotheses"),
    ],
)
def test_zero_shot_validates_each_hypothesis_list(texts: list[str], error: str) -> None:
    payload = {
        "mode": "zero-shot",
        "inputs": [PREMISE],
        "hypotheses": {"phishing": texts},
    }
    with build_client() as client:
        response = client.post("/classify", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == error


def test_zero_shot_caps_hypotheses_per_label() -> None:
    payload = {
        "mode": "zero-shot",
        "inputs": [PREMISE],
        "hypotheses": {"phishing": [f"claim number {i}" for i in range(9)]},
    }
    with build_client() as client:
        response = client.post("/classify", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "too_many_hypotheses"


def test_zero_shot_rejects_labels_that_collide_after_trimming() -> None:
    payload = {
        "mode": "zero-shot",
        "inputs": [PREMISE],
        "hypotheses": {"phishing": ["a claim"], " phishing": ["another claim"]},
    }
    with build_client() as client:
        response = client.post("/classify", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "duplicate_labels"


def test_zero_shot_pair_budget_counts_every_hypothesis() -> None:
    """Each phrasing is its own forward pass, so the budget must expand."""
    payload = {
        "mode": "zero-shot",
        "inputs": [f"document {i}" for i in range(10)],
        "hypotheses": {
            f"label{i}": [f"claim {i}.{n}" for n in range(5)] for i in range(6)
        },
    }
    with build_client() as client:
        response = client.post("/classify", json=payload)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "too_many_pairs"
    assert detail["got"] == 10 * 6 * 5
