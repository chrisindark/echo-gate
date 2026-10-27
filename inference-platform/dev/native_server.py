"""Native dev backend for the shared inference contract.

Runs on the Mac host so the M4 keeps Metal for the small models and, more
importantly, so the gateway stops paying ~2.2 GB of torch weights per process.

Scope discipline: this module implements `contract/INFERENCE_CONTRACT.md` and
nothing else. It intentionally has no batching scheduler, no queueing, no
multi-model routing beyond the roles in `models.lock.yaml`. Those are Infinity's
job in production. If a behaviour is needed by consumers, it belongs in the
contract first.

Routes:
    GET  /health      readiness + which roles are resident
    GET  /models      effective model lock (for version-skew logging)
    POST /embeddings  OpenAI-compatible dense embeddings
    POST /rerank      Cohere-compatible reranking, sigmoid-normalized
    POST /classify    NLI probabilities over labelled classes
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sentence_transformers import CrossEncoder, SentenceTransformer

logger = logging.getLogger("inference.native")

CONTRACT_VERSION = "1"
LOCK_PATH = Path(__file__).resolve().parent.parent / "models.lock.yaml"
MAX_DOCUMENTS = 256

# Roles this server should load. Default: every role in the lock. Trimming it is
# useful on a 16 GB laptop when only the cache path is being exercised.
DEFAULT_ROLES = ("embed", "rerank", "relevance", "nli")


# --------------------------------------------------------------------------- #
# model registry
# --------------------------------------------------------------------------- #


class Role:
    """One loaded model plus the calibration the contract promises."""

    def __init__(self, name: str, spec: dict[str, Any], device: str) -> None:
        self.name = name
        self.spec = spec
        self.model_id: str = spec["model"]
        self.revision: str | None = spec.get("revision") or None
        self.kind = "embedder" if spec.get("dim") else "cross-encoder"
        self.labels: list[str] = list(spec.get("labels") or [])
        self.normalize = bool(spec.get("normalize", False))
        self._lock = threading.Lock()

        if self.kind == "embedder":
            self.model = SentenceTransformer(
                self.model_id, revision=self.revision, device=device
            )
            self.dim: int | None = self.model.get_sentence_embedding_dimension()
            if spec.get("dim") and self.dim and int(spec["dim"]) != self.dim:
                raise RuntimeError(
                    f"role {name}: model emits dim={self.dim} but lock declares "
                    f"dim={spec['dim']} — vectors would not match existing storage"
                )
        else:
            self.model = CrossEncoder(
                self.model_id, revision=self.revision, device=device
            )
            self.dim = None

        self.device = device
        logger.info(
            "role %s ready: %s (%s, %s)", name, self.model_id, self.kind, device
        )

    def encode(self, texts: list[str], batch_size: int) -> np.ndarray:
        with self._lock:
            return self.model.encode(
                texts,
                batch_size=batch_size,
                normalize_embeddings=self.normalize,
                convert_to_numpy=True,
                show_progress_bar=False,
            )

    def predict(self, pairs: list[tuple[str, str]], batch_size: int) -> np.ndarray:
        with self._lock:
            scores = self.model.predict(
                pairs, batch_size=batch_size, show_progress_bar=False
            )
        return np.asarray(scores)

    def describe(self, loaded: bool) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "revision": self.revision,
            "loaded": loaded,
            "device": self.device,
            "dim": self.dim,
            "labels": self.labels or None,
        }


class Registry:
    def __init__(self) -> None:
        self.roles: dict[str, Role] = {}
        self.failed: dict[str, str] = {}
        self.spec: dict[str, Any] = {}

    def require(
        self, model_id: str | None, default_role: str, role_name: str | None = None
    ) -> Role:
        """Resolve a request to a loaded role.

        Precedence: explicit ``model`` -> explicit ``role`` -> the endpoint's
        default role. Accepting a role is what stops distinct roles from
        collapsing: without it an unpinned ``/rerank`` call is always served by
        the default reranker, so a consumer asking for the relevance model would
        silently get the primary one.
        """
        if model_id is not None:
            for role in self.roles.values():
                if role.model_id == model_id:
                    return role
            # An explicit but unknown model is a caller bug, not a load failure.
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unknown_model",
                    "model": model_id,
                    "available": sorted(r.model_id for r in self.roles.values()),
                },
            )

        target = role_name or default_role
        role = self.roles.get(target)
        if role is not None:
            return role

        declared = self.spec.get("roles") or {}
        if target not in declared:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unknown_role",
                    "role": target,
                    "declared": sorted(declared),
                },
            )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "model_not_loaded",
                "role": target,
                "loaded": sorted(self.roles),
                "unloaded": sorted(self.failed),
            },
        )


registry = Registry()


def resolve_device() -> str:
    """auto -> mps when available, else cpu.

    MPS is a wash for the sub-100M cross-encoders at batch 1 and a real win for
    nothing else in the default lock, so it is not worth forcing. It is still the
    better default when a caller batches heavily.
    """
    requested = os.getenv("INFERENCE_DEVICE", "auto").strip().lower()
    if requested != "auto":
        return requested
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except Exception:  # pragma: no cover - torch always present via s-t
        logger.debug("torch MPS probe failed; using cpu", exc_info=True)
    return "cpu"


def load_roles() -> None:
    spec = yaml.safe_load(LOCK_PATH.read_text())
    registry.spec = spec
    device = resolve_device()
    wanted = [
        r.strip()
        for r in os.getenv("INFERENCE_ROLES", ",".join(DEFAULT_ROLES)).split(",")
        if r.strip()
    ]
    for name in wanted:
        role_spec = (spec.get("roles") or {}).get(name)
        if not role_spec:
            registry.failed[name] = "not_declared_in_models_lock"
            logger.warning("role %s requested but absent from models.lock.yaml", name)
            continue
        try:
            registry.roles[name] = Role(name, role_spec, device)
        except Exception as exc:  # a missing model must not kill the process
            registry.failed[name] = str(exc)
            logger.error("role %s failed to load: %s", name, exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await asyncio.to_thread(load_roles)
    ready = sorted(registry.roles)
    logger.info(
        "inference ready on %s; roles=%s failed=%s",
        resolve_device(),
        ready,
        sorted(registry.failed),
    )
    yield


app = FastAPI(title="inference-platform", version=CONTRACT_VERSION, lifespan=lifespan)


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #


class EmbeddingsRequest(BaseModel):
    model: str | None = None
    role: str | None = None
    input: str | list[str]
    dimensions: int | None = None
    batch_size: int = Field(default=32, ge=1, le=256)


class RerankRequest(BaseModel):
    model: str | None = None
    role: str | None = None
    query: str = Field(min_length=1)
    documents: list[str] = Field(min_length=1, max_length=MAX_DOCUMENTS)
    top_n: int | None = Field(default=None, ge=1)
    return_documents: bool = False
    batch_size: int = Field(default=32, ge=1, le=256)


class ClassifyRequest(BaseModel):
    model: str | None = None
    role: str | None = None
    pairs: list[tuple[str, str]] = Field(min_length=1, max_length=MAX_DOCUMENTS)
    batch_size: int = Field(default=32, ge=1, le=256)


# --------------------------------------------------------------------------- #
# numeric helpers
# --------------------------------------------------------------------------- #


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _softmax(values: list[float]) -> list[float]:
    m = max(values)
    exps = [math.exp(v - m) for v in values]
    total = sum(exps) or 1.0
    return [e / total for e in exps]


def _token_count(role: Role, texts: list[str]) -> int:
    try:
        tokenizer = getattr(role.model, "tokenizer", None)
        if tokenizer is not None:
            return sum(len(ids) for ids in tokenizer(texts)["input_ids"])
    except Exception:  # pragma: no cover - usage counts are advisory
        logger.debug("token counting unavailable", exc_info=True)
    return sum(len(t.split()) for t in texts)


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #


@app.get("/health")
async def health() -> Any:
    loaded = sorted(registry.roles)
    degraded = bool(registry.failed) or not loaded
    body = {
        "status": "degraded" if degraded else "ok",
        "version": CONTRACT_VERSION,
        "roles": {name: role.describe(True) for name, role in registry.roles.items()},
    }
    if registry.failed:
        body["unloaded"] = registry.failed
    if degraded:
        raise HTTPException(status_code=503, detail=body)
    return body


@app.get("/models")
async def models() -> Any:
    return {
        "version": CONTRACT_VERSION,
        "device": resolve_device(),
        "roles": registry.spec.get("roles", {}),
        "loaded": {
            name: {"model": r.model_id, "revision": r.revision, "dim": r.dim}
            for name, r in registry.roles.items()
        },
        "failed": registry.failed,
    }


@app.post("/embeddings")
async def embeddings(request: EmbeddingsRequest) -> Any:
    role = registry.require(request.model, "embed", request.role)
    if role.kind != "embedder":
        raise HTTPException(
            status_code=400,
            detail={"error": "not_an_embedding_model", "model": role.model_id},
        )

    texts = [request.input] if isinstance(request.input, str) else request.input
    if not texts:
        return {
            "object": "list",
            "model": role.model_id,
            "data": [],
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    vectors = await asyncio.to_thread(role.encode, texts, request.batch_size)
    if request.dimensions is not None and vectors.shape[1] != request.dimensions:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "dimension_mismatch",
                "requested": request.dimensions,
                "available": int(vectors.shape[1]),
            },
        )

    tokens = _token_count(role, texts)
    return {
        "object": "list",
        "model": role.model_id,
        "data": [
            {"object": "embedding", "index": i, "embedding": vector.tolist()}
            for i, vector in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


@app.post("/rerank")
async def rerank(request: RerankRequest) -> Any:
    role = registry.require(request.model, "rerank", request.role)
    if role.kind == "embedder":
        raise HTTPException(
            status_code=400,
            detail={"error": "not_a_cross_encoder", "model": role.model_id},
        )

    pairs = [(request.query, document) for document in request.documents]
    scores = await asyncio.to_thread(role.predict, pairs, request.batch_size)
    flat = scores.reshape(-1).tolist() if scores.ndim > 1 else scores.tolist()

    ranked = sorted(
        (
            {"index": i, "score": _sigmoid(float(raw)), "score_raw": float(raw)}
            for i, raw in enumerate(flat)
        ),
        key=lambda item: item["score"],
        reverse=True,
    )
    if request.top_n is not None:
        ranked = ranked[: request.top_n]
    if request.return_documents:
        for item in ranked:
            item["document"] = {"text": request.documents[item["index"]]}

    return {
        "results": ranked,
        "meta": {
            "model": role.model_id,
            "billed_units": {"documents": len(request.documents)},
        },
    }


@app.post("/classify")
async def classify(request: ClassifyRequest) -> Any:
    role = registry.require(request.model, "nli", request.role)
    if not role.labels:
        raise HTTPException(
            status_code=400,
            detail={"error": "no_labels_declared", "model": role.model_id},
        )

    pairs = [(premise, hypothesis) for premise, hypothesis in request.pairs]
    scores = await asyncio.to_thread(role.predict, pairs, request.batch_size)
    rows = scores.tolist() if scores.ndim > 1 else [[s] for s in scores.tolist()]

    results = []
    for index, row in enumerate(rows):
        logits = [float(v) for v in row[: len(role.labels)]]
        probs = _softmax(logits)
        results.append(
            {
                "index": index,
                "probabilities": dict(zip(role.labels, probs)),
                "label": role.labels[max(range(len(probs)), key=probs.__getitem__)],
                "logits": logits,
            }
        )

    return {"labels": role.labels, "results": results}
