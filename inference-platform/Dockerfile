# syntax=docker/dockerfile:1
#
# Native inference server as a container: dense embeddings, sparse BM25, rerank,
# and NLI over HTTP, CPU only.
#
#   docker build -t inference-platform:0.1.0 .
#   docker build -t inference-platform:0.1.0 --build-arg PREFETCH=1 .   # weights baked in
#   docker run --rm -p 7997:7997 inference-platform:0.1.0
#
# A GPU production deployment should use Infinity (deploy/compose/inference.yml):
# it batches, and this server deliberately does not. This image is for laptops,
# VMs, and Kubernetes clusters without a GPU, and for keeping the contract
# reachable when a GPU pool is not worth it.
#
# The image is not "small" and cannot be: sentence-transformers brings torch
# (~900 MB unpacked) and fastembed brings onnxruntime. That is the cost of serving
# encoders in one process instead of one copy per consumer process; see README.

FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependency layer first: it only invalidates when the lock or metadata changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY models.lock.yaml ./
COPY src ./src
# --no-editable installs the package into site-packages, so the runtime image
# needs neither `src/` nor a .pth file pointing at it.
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.11-slim AS runtime

# PREFETCH=1 downloads every role's weights into the image. Trade ~2.5 GB of image
# size for a rollout that answers immediately; leave it off when the weights come
# from a mounted volume or an init container.
ARG PREFETCH=0

# torch and onnxruntime need libgomp at import time; nothing else is required.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    INFERENCE_HOST=0.0.0.0 \
    INFERENCE_PORT=7997 \
    INFERENCE_LOCK_PATH=/app/models.lock.yaml \
    INFERENCE_MODEL_CACHE=/models

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/models.lock.yaml /app/models.lock.yaml

RUN useradd --create-home --uid 10001 inference \
    && mkdir -p /models \
    && chown -R inference:inference /models

USER inference

RUN if [ "$PREFETCH" = "1" ]; then inference-platform-prefetch; fi

EXPOSE 7997

# /health answers 503 while a role is unloaded, which is exactly the signal a
# container health check wants.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD python -c "import os,sys,urllib.request; \
port=os.getenv('INFERENCE_PORT','7997'); \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=4).status==200 else 1)"

ENTRYPOINT ["inference-platform"]
