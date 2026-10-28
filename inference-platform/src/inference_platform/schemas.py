"""Request models. Responses are plain dicts so that optional keys can be omitted."""

from __future__ import annotations

from pydantic import BaseModel, Field

from inference_platform.settings import MAX_DOCUMENTS


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
    model: str | None = None
    role: str | None = None
    pairs: list[tuple[str, str]] = Field(min_length=1, max_length=MAX_DOCUMENTS)
    batch_size: int = Field(default=32, ge=1, le=256)
