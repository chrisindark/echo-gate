# ADR 0002: Out-of-Process HTTP Inference Platform

## Status
Accepted (2026-10-18)

## Context
Running dense sentence embeddings (`BAAI/bge-small-en-v1.5`), cross-encoder rerankers, and NLI judges in-process within the FastAPI gateway pulls over 5GB of CUDA/PyTorch dependencies into every container image (inflating image size from 0.7GB to 9.4GB) and introduces CPU/GPU resource contention with the async event loop.

## Decision
1. Dense vectorization and cross-encoder inference are decoupled into a dedicated HTTP microservice (`inference-platform/`).
2. The gateway connects over HTTP via `InferenceClient`.
3. In-process fallback is strictly prohibited. If the inference service is unavailable, the gateway fails open to the direct LLM provider path.

## Consequences
- Gateway images stay lightweight (~0.7GB) and fast to deploy.
- Clear operational separation between async I/O networking and heavy ML inference.
