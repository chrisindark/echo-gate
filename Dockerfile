# syntax=docker/dockerfile:1

# --- dependency stage ---------------------------------------------------------
# Builds a virtualenv that contains only the gateway's *main* dependencies.
# Poetry stays behind in this stage; the runtime image gets the venv and nothing
# else (no poetry, no pip, no wheel cache).
FROM python:3.11-slim AS deps

ENV PIP_NO_CACHE_DIR=1
WORKDIR /build

# Poetry is pinned to the major that produced poetry.lock. The lock is
# `lock-version = "2.1"`, which Poetry 1.x cannot read, so 1.8.3 could never
# install this project. It also needs the export plugin: the dependencies are
# installed into a venv created here, not into the one poetry would make in its
# own cache. Copying that cache venv would carry absolute shebangs pointing at a
# path that does not exist in the runtime image ("exec .../uvicorn: no such file
# or directory"), so the lock is exported and installed instead.
RUN pip install "poetry==2.4.1" "poetry-plugin-export==1.10.1"

RUN python -m venv /opt/venv

COPY pyproject.toml poetry.lock ./

# No torch, transformers or sentence-transformers: dense embeddings, reranking,
# relevance, and NLI are served by `inference-platform/` over HTTP, and the app
# refuses to start without it. That is why this image is ~0.7GB instead of 9.4GB.
RUN poetry export --only main --format requirements.txt \
        --output /tmp/requirements.txt --without-hashes ; \
    /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt ; \
    # Nothing installs packages at runtime, so pip/setuptools stay out of the
    # runtime image. This has to happen here, not after the copy: a later layer
    # that deletes files does not shrink the image.
    /opt/venv/bin/pip uninstall -y pip setuptools wheel

# --- runtime stage ------------------------------------------------------------
FROM python:3.11-slim

# PROJECT_NAME is set here because FastAPI asserts on an empty OpenAPI title at
# import time; the image carries the default rather than depending on it being
# injected at run time.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROJECT_NAME=echo-gate \
    HF_HOME=/workspace/.model_cache \
    BM25_MODEL_PATH=/workspace/.model_cache/bm25 \
    BM25_LOCAL_FILES_ONLY=true \
    PYTHONPATH=/workspace/api \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /workspace

COPY --from=deps /opt/venv /opt/venv

# The only model that still runs in-process is sparse BM25: a small ONNX
# tokenizer, no torch.
#
# It is staged into a stable directory and pinned with BM25_MODEL_PATH rather than
# left to fastembed's own cache resolution. `Qdrant/bm25` declares
# `model_file="mock.file"`, which does not exist in the repo, so fastembed's
# Hugging Face branch can never satisfy its own sanity check; the only layout it
# accepts for `local_files_only=True` is an extracted `<cache_dir>/bm25`, and
# relying on that internal is how the first version of this stage silently baked
# a model the runtime could not find.
RUN python - <<'PY'
import shutil
from pathlib import Path

from fastembed import SparseTextEmbedding

model = SparseTextEmbedding(model_name="Qdrant/bm25", cache_dir="/workspace/.model_cache/fastembed")
resolved = Path(model.model._model_dir)
staged = Path("/workspace/.model_cache/bm25")
# copytree without symlinks dereferences the HF blob links into real files, so the
# staged directory is self-contained.
shutil.copytree(resolved, staged, dirs_exist_ok=True)
print(f"staged BM25 from {resolved} -> {staged} ({len(list(staged.iterdir()))} files)")
PY

COPY . .

EXPOSE 8000

# Requires INFERENCE_BASE_URL to point at the inference platform. Without it the
# gateway refuses to start (`InferenceConfigError`): there is no in-process model
# path to fall back to.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
