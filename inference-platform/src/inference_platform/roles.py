"""Roles: the mapping from "which model answers role X" to a loaded model.

Three kinds of role live here, matching the three things this service does:

    embedder       dense vectors              (sentence-transformers)
    cross-encoder  pairwise scores            (sentence-transformers CrossEncoder)
    sparse         lexical indices + weights  (fastembed, no torch)

`kind` comes from `models.lock.yaml` so the loader never has to guess. Model
imports are deliberately lazy: `import inference_platform.api` stays cheap, a
missing model surfaces as one failed role instead of a dead process, and the
tests can exercise the HTTP layer with stand-in roles and no torch download.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

import numpy as np

from inference_platform.settings import model_cache_dir

logger = logging.getLogger("inference.roles")

KINDS = ("embedder", "cross-encoder", "sparse")


def _fastembed_version() -> str:
    try:
        import fastembed

        return str(fastembed.__version__)
    except Exception:  # pragma: no cover - version is advisory
        return "unknown"


def role_kind(spec: dict[str, Any]) -> str:
    """Declared kind, else inferred from the shape of the lock entry.

    Inference keeps older `models.lock.yaml` files working: a role with a `dim` is
    a dense embedder, a role without one is a cross-encoder. New entries should
    declare `kind` explicitly so the lock stays self-describing.
    """
    declared = (spec.get("kind") or "").strip().lower()
    if declared:
        if declared not in KINDS:
            raise RuntimeError(
                f"unknown role kind {declared!r}; expected one of {KINDS}"
            )
        return declared
    return "embedder" if spec.get("dim") else "cross-encoder"


class _Role:
    kind = ""

    def __init__(self, name: str, spec: dict[str, Any], device: str) -> None:
        self.name = name
        self.spec = spec
        self.model_id: str = spec["model"]
        self.revision: str | None = spec.get("revision") or None
        self.device = device
        # Declared dimension. DenseRole replaces this with the dimension the model
        # actually emits (and refuses to load if the two disagree); cross-encoders
        # declare `dim: null` and keep it.
        self.dim: int | None = spec.get("dim")
        self.labels: list[str] = list(spec.get("labels") or [])
        self.normalize = bool(spec.get("normalize", False))
        # sentence-transformers models are not thread-safe; the server runs them
        # via to_thread, so every forward pass is serialized behind this lock.
        self._lock = threading.Lock()

    def encode(self, texts: list[str], batch_size: int) -> np.ndarray:
        raise NotImplementedError

    def predict(self, pairs: list[tuple[str, str]], batch_size: int) -> np.ndarray:
        raise NotImplementedError

    def encode_sparse(self, texts: list[str], batch_size: int) -> list[dict[str, list]]:
        raise NotImplementedError

    def describe(self, loaded: bool) -> dict[str, Any]:
        info = {
            "model": self.model_id,
            "revision": self.revision,
            "kind": self.kind,
            "loaded": loaded,
            "device": self.device,
            "dim": self.dim,
            "labels": self.labels or None,
        }
        if self.kind == "sparse":
            # The tokenizer build *is* part of the vector space for sparse roles,
            # so it is reported next to the model id: two services on different
            # fastembed versions produce incomparable indices.
            info["backend"] = f"fastembed {_fastembed_version()}"
        return info


class DenseRole(_Role):
    kind = "embedder"

    def __init__(self, name: str, spec: dict[str, Any], device: str) -> None:
        super().__init__(name, spec, device)
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(
            self.model_id, revision=self.revision, device=device
        )
        self.dim = self.model.get_sentence_embedding_dimension()
        if spec.get("dim") and self.dim and int(spec["dim"]) != self.dim:
            raise RuntimeError(
                f"role {name}: model emits dim={self.dim} but lock declares "
                f"dim={spec['dim']} — vectors would not match existing storage"
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


class CrossEncoderRole(_Role):
    kind = "cross-encoder"

    def __init__(self, name: str, spec: dict[str, Any], device: str) -> None:
        super().__init__(name, spec, device)
        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(self.model_id, revision=self.revision, device=device)

    def predict(self, pairs: list[tuple[str, str]], batch_size: int) -> np.ndarray:
        with self._lock:
            scores = self.model.predict(
                pairs, batch_size=batch_size, show_progress_bar=False
            )
        return np.asarray(scores)


class SparseRole(_Role):
    """BM25 lexical vectors: `indices` into the model's vocab, `values` weights.

    Version skew is the whole risk here. A sparse vector is only comparable to
    another produced by the *same tokenizer build*, so a consumer that already
    wrote vectors with a different fastembed version must not point at this role
    without re-indexing. See README for why Echo Gate keeps its BM25 local.
    """

    kind = "sparse"

    def __init__(self, name: str, spec: dict[str, Any], device: str) -> None:
        super().__init__(name, spec, device)
        from fastembed import SparseTextEmbedding

        shared_cache = model_cache_dir()
        cache_dir = os.getenv("SPARSE_CACHE_PATH", "").strip() or (
            os.path.join(shared_cache, "fastembed") if shared_cache else None
        )
        specific_model_path = os.getenv("SPARSE_MODEL_PATH", "").strip() or None
        local_files_only = os.getenv("SPARSE_LOCAL_FILES_ONLY", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        model_kwargs: dict[str, Any] = {
            "model_name": self.model_id,
            "local_files_only": local_files_only,
        }
        if cache_dir:
            model_kwargs["cache_dir"] = cache_dir
        if specific_model_path:
            model_kwargs["specific_model_path"] = specific_model_path

        self.model = SparseTextEmbedding(**model_kwargs)
        self.dim = None

    def encode_sparse(self, texts: list[str], batch_size: int) -> list[dict[str, list]]:
        with self._lock:
            embeddings = list(self.model.embed(texts, batch_size=batch_size))
        return [
            {
                "indices": np.asarray(item.indices).astype(int).tolist(),
                "values": np.asarray(item.values).astype(float).tolist(),
            }
            for item in embeddings
        ]


_LOADERS = {
    "embedder": DenseRole,
    "cross-encoder": CrossEncoderRole,
    "sparse": SparseRole,
}


class Registry:
    """Loaded roles for this process, plus the failures it survived."""

    def __init__(self) -> None:
        self.roles: dict[str, _Role] = {}
        self.failed: dict[str, str] = {}
        self.spec: dict[str, Any] = {}

    def load(self, spec: dict[str, Any], wanted: list[str], device: str) -> None:
        self.spec = spec
        for name in wanted:
            role_spec = (spec.get("roles") or {}).get(name)
            if not role_spec:
                self.failed[name] = "not_declared_in_models_lock"
                logger.warning("role %s requested but absent from the lock", name)
                continue
            try:
                loader = _LOADERS[role_kind(role_spec)]
                self.roles[name] = loader(name, role_spec, device)
            except Exception as exc:  # a missing model must not kill the process
                self.failed[name] = f"{type(exc).__name__}: {exc}"
                logger.error("role %s failed to load: %s", name, exc)
                continue
            logger.info(
                "role %s ready: %s (%s, %s)",
                name,
                self.roles[name].model_id,
                self.roles[name].kind,
                device,
            )

    def require(
        self, model_id: str | None, default_role: str, role_name: str | None = None
    ) -> _Role:
        """Resolve a request to a loaded role.

        Precedence: explicit ``model`` -> explicit ``role`` -> the endpoint's
        default role. Accepting a role is what stops distinct roles from
        collapsing: without it an unpinned ``/rerank`` call is always served by
        the default reranker, so a consumer asking for the relevance model would
        silently get the primary one.
        """
        from inference_platform.errors import api_error

        if model_id is not None:
            for role in self.roles.values():
                if role.model_id == model_id:
                    return role
            # An explicit but unknown model is a caller bug, not a load failure.
            raise api_error(
                400,
                "unknown_model",
                model=model_id,
                available=sorted(r.model_id for r in self.roles.values()),
            )

        target = role_name or default_role
        role = self.roles.get(target)
        if role is not None:
            return role

        declared = self.spec.get("roles") or {}
        if target not in declared:
            raise api_error(400, "unknown_role", role=target, declared=sorted(declared))
        raise api_error(
            503,
            "model_not_loaded",
            role=target,
            loaded=sorted(self.roles),
            unloaded=sorted(self.failed),
        )
