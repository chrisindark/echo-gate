"""Request models. Responses are plain dicts so that optional keys can be omitted."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from inference_platform.settings import MAX_DOCUMENTS

DEFAULT_HYPOTHESIS_TEMPLATE = "This text is about {}."


class EmbeddingsRequest(BaseModel):
    """Dense and/or sparse vectors for a batch of texts.

    `dense` defaults to true so a consumer written against the original contract
    (which only knew dense) keeps working byte-for-byte: it sends `input` and gets
    `data` back, with no extra keys required of it.

    Sparse is requested explicitly and answered in the *same* response, which is
    the point: a hybrid consumer pays one round trip, not two.
    """

    model: str | None = None
    role: str | None = None
    input: str | list[str]
    dimensions: int | None = None
    batch_size: int = Field(default=32, ge=1, le=256)
    dense: bool = True
    sparse: bool = False
    # Optional pin for the sparse branch, mirroring `model` for the dense one.
    sparse_model: str | None = None


class RerankRequest(BaseModel):
    model: str | None = None
    role: str | None = None
    query: str = Field(min_length=1)
    documents: list[str] = Field(min_length=1, max_length=MAX_DOCUMENTS)
    top_n: int | None = Field(default=None, ge=1)
    return_documents: bool = False
    batch_size: int = Field(default=32, ge=1, le=256)


class ClassifyRequest(BaseModel):
    """NLI, in one of two modes.

    ``pairs`` is the original contract: explicit premise/hypothesis pairs, one
    row of label probabilities each. Echo Gate uses it to check a cached answer
    against a new prompt.

    ``zero-shot`` turns the NLI model into a classifier without training anything:
    each candidate label becomes the hypothesis of a pair, and the entailment
    probability of that pair is the label's score. Used for email/document triage
    where a generative model would be slower, larger, and less predictable.

    The two shapes are validated in the handler rather than by Pydantic so that a
    missing field is a structured `400` naming the problem, not a `422` blob.
    """

    model: str | None = None
    role: str | None = None
    mode: Literal["pairs", "zero-shot"] = "pairs"
    batch_size: int = Field(default=32, ge=1, le=256)

    # mode="pairs"
    pairs: list[tuple[str, str]] | None = None

    # mode="zero-shot"
    inputs: list[str] | None = None
    labels: list[str] | None = None
    hypothesis_template: str = DEFAULT_HYPOTHESIS_TEMPLATE
