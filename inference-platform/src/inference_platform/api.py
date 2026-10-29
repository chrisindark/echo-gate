"""The HTTP surface: `contract/INFERENCE_CONTRACT.md` implemented.

Build the app with `create_app()` for a normal server, or with an injected
`Registry` and `load_models=False` to exercise the routes without downloading
weights (that is what `tests/` does).
"""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

from inference_platform.errors import api_error
from inference_platform.roles import Registry, _Role
from inference_platform.schemas import ClassifyRequest, EmbeddingsRequest, RerankRequest
from inference_platform.settings import (
    CONTRACT_VERSION,
    MAX_LABELS,
    MAX_PAIRS,
    MAX_ZERO_SHOT_INPUTS,
    load_lock,
    prepare_cache_env,
    resolve_device,
    wanted_roles,
)

logger = logging.getLogger("inference.api")


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


def _token_count(role: _Role | None, texts: list[str]) -> int:
    """Advisory usage count: the model's tokenizer when it has one, else words."""
    try:
        tokenizer = getattr(role.model, "tokenizer", None) if role is not None else None
        if tokenizer is not None:
            return sum(len(ids) for ids in tokenizer(texts)["input_ids"])
    except Exception:  # pragma: no cover - usage counts are advisory
        logger.debug("token counting unavailable", exc_info=True)
    return sum(len(t.split()) for t in texts)


def _as_texts(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def create_app(
    registry: Registry | None = None, *, load_models: bool = True
) -> FastAPI:
    reg = registry if registry is not None else Registry()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        device = resolve_device()
        if load_models:
            prepare_cache_env()
            spec = load_lock()
            await asyncio.to_thread(reg.load, spec, wanted_roles(spec), device)
        logger.info(
            "inference ready on %s; roles=%s failed=%s",
            device,
            sorted(reg.roles),
            sorted(reg.failed),
        )
        yield

    app = FastAPI(
        title="inference-platform", version=CONTRACT_VERSION, lifespan=lifespan
    )

    # ------------------------------------------------------------------ #
    # probes
    # ------------------------------------------------------------------ #

    @app.get("/health")
    async def health() -> Any:
        loaded = sorted(reg.roles)
        degraded = bool(reg.failed) or not loaded
        body: dict[str, Any] = {
            "status": "degraded" if degraded else "ok",
            "version": CONTRACT_VERSION,
            "roles": {name: role.describe(True) for name, role in reg.roles.items()},
        }
        if reg.failed:
            body["unloaded"] = reg.failed
        if degraded:
            raise HTTPException(status_code=503, detail=body)
        return body

    @app.get("/models")
    async def models() -> Any:
        return {
            "version": CONTRACT_VERSION,
            "device": resolve_device(),
            "roles": reg.spec.get("roles", {}),
            "loaded": {
                name: {
                    "model": role.model_id,
                    "revision": role.revision,
                    "kind": role.kind,
                    "dim": role.dim,
                }
                for name, role in reg.roles.items()
            },
            "failed": reg.failed,
        }

    # ------------------------------------------------------------------ #
    # /embeddings: dense, sparse, or both in one round trip
    # ------------------------------------------------------------------ #

    @app.post("/embeddings")
    async def embeddings(request: EmbeddingsRequest) -> Any:
        if not request.dense and not request.sparse:
            raise api_error(
                400,
                "nothing_requested",
                message=(
                    "set dense and/or sparse; refusing a call that computes neither"
                ),
            )

        texts = _as_texts(request.input)
        body: dict[str, Any] = {"object": "list"}
        tokens = 0

        if request.dense:
            role = reg.require(request.model, "embed", request.role)
            if role.kind != "embedder":
                raise api_error(400, "not_an_embedding_model", model=role.model_id)

            body["model"] = role.model_id
            if texts:
                vectors = await asyncio.to_thread(
                    role.encode, texts, request.batch_size
                )
                if (
                    request.dimensions is not None
                    and vectors.shape[1] != request.dimensions
                ):
                    raise api_error(
                        400,
                        "dimension_mismatch",
                        requested=request.dimensions,
                        available=int(vectors.shape[1]),
                    )
                body["data"] = [
                    {"object": "embedding", "index": i, "embedding": vector.tolist()}
                    for i, vector in enumerate(vectors)
                ]
            else:
                body["data"] = []
            tokens = _token_count(role, texts)

        if request.sparse:
            # Resolved outside the try: an unknown or unloaded sparse role is a
            # deployment/caller error that is identical on every request, so it
            # must be visible rather than swallowed into `sparse_error`.
            sparse_role = reg.require(request.sparse_model, "sparse")
            if sparse_role.kind != "sparse":
                raise api_error(400, "not_a_sparse_model", model=sparse_role.model_id)

            body["sparse_model"] = sparse_role.model_id
            if not texts:
                body["sparse"] = []
            else:
                try:
                    rows = await asyncio.to_thread(
                        sparse_role.encode_sparse, texts, request.batch_size
                    )
                except Exception as exc:
                    # Transient failure. Partial success beats no answer: a hybrid
                    # consumer falls back to dense, and dense-only consumers are
                    # unaffected because they never ask for sparse.
                    logger.exception("sparse branch failed; returning dense only")
                    if not request.dense:
                        raise api_error(
                            500,
                            "inference_failed",
                            branch="sparse",
                            model=sparse_role.model_id,
                            message=f"{type(exc).__name__}: {exc}",
                        ) from exc
                    body["sparse_error"] = f"{type(exc).__name__}: {exc}"
                else:
                    body["sparse"] = [
                        {"index": index, **row} for index, row in enumerate(rows)
                    ]
            if not request.dense:
                tokens = _token_count(None, texts)

        body["usage"] = {"prompt_tokens": tokens, "total_tokens": tokens}
        return body

    # ------------------------------------------------------------------ #
    # /rerank
    # ------------------------------------------------------------------ #

    @app.post("/rerank")
    async def rerank(request: RerankRequest) -> Any:
        role = reg.require(request.model, "rerank", request.role)
        if role.kind != "cross-encoder":
            raise api_error(400, "not_a_cross_encoder", model=role.model_id)

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

    # ------------------------------------------------------------------ #
    # /classify: explicit NLI pairs, or zero-shot labels via hypotheses
    # ------------------------------------------------------------------ #

    @app.post("/classify")
    async def classify(request: ClassifyRequest) -> Any:
        role = reg.require(request.model, "nli", request.role)
        if not role.labels:
            raise api_error(400, "no_labels_declared", model=role.model_id)
        if request.mode == "zero-shot":
            return await _classify_zero_shot(role, request)
        return await _classify_pairs(role, request)

    async def _classify_pairs(role: _Role, request: ClassifyRequest) -> Any:
        pairs = [(premise, hypothesis) for premise, hypothesis in request.pairs or []]
        if not pairs:
            raise api_error(
                400,
                "missing_pairs",
                message='mode="pairs" requires `pairs`; omit `mode` for this shape',
            )
        if len(pairs) > MAX_PAIRS:
            raise api_error(400, "too_many_pairs", limit=MAX_PAIRS, got=len(pairs))

        scores = await asyncio.to_thread(role.predict, pairs, request.batch_size)
        rows = scores.tolist() if scores.ndim > 1 else [[s] for s in scores.tolist()]

        results = []
        for index, row in enumerate(rows):
            logits = [float(v) for v in row[: len(role.labels)]]
            probs = _softmax(logits)
            results.append(
                {
                    "index": index,
                    "probabilities": dict(zip(role.labels, probs, strict=False)),
                    "label": role.labels[max(range(len(probs)), key=probs.__getitem__)],
                    "logits": logits,
                }
            )

        return {"mode": "pairs", "labels": role.labels, "results": results}

    async def _classify_zero_shot(role: _Role, request: ClassifyRequest) -> Any:
        inputs = list(request.inputs or [])
        labels = [label.strip() for label in (request.labels or [])]
        template = request.hypothesis_template

        if not inputs:
            raise api_error(
                400, "missing_inputs", message='mode="zero-shot" requires `inputs`'
            )
        if not labels:
            raise api_error(
                400, "missing_labels", message='mode="zero-shot" requires `labels`'
            )
        if any(not label for label in labels):
            raise api_error(400, "empty_label", message="labels must not be blank")
        if len(set(labels)) != len(labels):
            # Duplicates would silently collapse in the `scores` object, so the
            # caller gets an error instead of a score that vanished.
            raise api_error(400, "duplicate_labels", labels=labels)
        if "{}" not in template:
            raise api_error(
                400,
                "bad_hypothesis_template",
                message="`hypothesis_template` must contain a `{}` placeholder",
                template=template,
            )
        if len(inputs) > MAX_ZERO_SHOT_INPUTS:
            raise api_error(
                400, "too_many_inputs", limit=MAX_ZERO_SHOT_INPUTS, got=len(inputs)
            )
        if len(labels) > MAX_LABELS:
            raise api_error(400, "too_many_labels", limit=MAX_LABELS, got=len(labels))
        budget = len(inputs) * len(labels)
        if budget > MAX_PAIRS:
            raise api_error(
                400,
                "too_many_pairs",
                limit=MAX_PAIRS,
                got=budget,
                message="inputs * labels must stay within the pair budget",
            )

        entailment_index = next(
            (
                index
                for index, label in enumerate(role.labels)
                if label.lower() == "entailment"
            ),
            None,
        )
        if entailment_index is None:
            raise api_error(
                400,
                "no_entailment_label",
                model=role.model_id,
                labels=role.labels,
                message="zero-shot scoring needs an `entailment` class in the NLI role",
            )

        # One forward pass per (input, label). The whole grid goes in one batched
        # call: per-pair requests would pay the model's fixed overhead N times.
        pairs = [(text, template.format(label)) for text in inputs for label in labels]
        scores = await asyncio.to_thread(role.predict, pairs, request.batch_size)
        rows = scores.tolist() if scores.ndim > 1 else [[s] for s in scores.tolist()]

        results = []
        for index in range(len(inputs)):
            window = rows[index * len(labels) : (index + 1) * len(labels)]
            entailment = []
            for row in window:
                logits = [float(v) for v in row[: len(role.labels)]]
                entailment.append(_softmax(logits)[entailment_index])
            # Per-label entailment probabilities are independent: three labels can
            # all score 0.8, because each is a separate NLI decision. The
            # normalized copy is a softmax across labels for "pick exactly one"
            # consumers; it is for ranking only, not for thresholding.
            normalized = _softmax(entailment)
            order = sorted(
                range(len(labels)), key=lambda i: entailment[i], reverse=True
            )
            results.append(
                {
                    "index": index,
                    "label": labels[order[0]],
                    "score": entailment[order[0]],
                    "scores": dict(zip(labels, entailment, strict=False)),
                    "scores_normalized": dict(zip(labels, normalized, strict=False)),
                    "ranked": [
                        {
                            "label": labels[i],
                            "score": entailment[i],
                            "score_normalized": normalized[i],
                        }
                        for i in order
                    ],
                }
            )

        return {
            "mode": "zero-shot",
            "labels": labels,
            "entailment_label": role.labels[entailment_index],
            "hypothesis_template": template,
            "results": results,
        }

    return app


app = create_app()
