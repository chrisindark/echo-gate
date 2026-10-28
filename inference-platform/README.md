# inference-platform

Shared model-serving layer for Echo Gate, doc-nexus, and agent-forge.

It is **not** an application. It owns no business logic, no cache decisions, and no
data. It holds model weights in memory and answers five HTTP routes. Consumers
import nothing from here except the wire contract
([`contract/INFERENCE_CONTRACT.md`](contract/INFERENCE_CONTRACT.md)).

It is also a **separate dependency root inside the echo-gate checkout** — its own
`pyproject.toml`, its own `uv.lock`, its own virtualenv. It is written to be moved
to its own repository unchanged: nothing here imports the gateway, and the gateway
never imports this package.

## What is shared, and what is not

| Shared here | Stays in the consumer |
| :--- | :--- |
| Dense embeddings (`/embeddings`, `dense: true`) | Cache keys, thresholds, retention policy |
| Sparse BM25 vectors (`/embeddings`, `sparse: true`) | Which candidates to send, fusion weights |
| Cross-encoder rerank (`/rerank`) | Which candidates to send |
| NLI / contradiction (`/classify`) | Whether a contradiction blocks a cache hit |
| — | Entity extraction (pure regex, microseconds) |
| — | Qdrant, Redis, Postgres (separate per app) |
| — | Generative calls (intent, judge) — those are not encoders |

### Why sparse is served here, and why Echo Gate ignores it for now

Dense and sparse come back from **one** request. A hybrid consumer pays one round
trip, not two, and the marginal cost of the sparse branch is ~1 ms of tokenizer
next to a batched dense pass.

The reason to centralize it is not latency, it is tokenizer identity. BM25 indices
are hashed vocabulary slots: vectors are only comparable to vectors written by the
*same* tokenizer build. Serving one build centrally is what stops three products
from drifting into three incompatible sparse spaces. `/health` reports the sparse
role's `backend` (e.g. `fastembed 0.9.1`) so a consumer can record it next to the
vectors it writes.

**Echo Gate itself keeps BM25 local** (`EmbeddingService`), because its Qdrant
collection already holds vectors written by the gateway's own fastembed build.
Moving the tokenizer without re-indexing would silently break hybrid search, so
that migration waits for a collection rebuild. doc-nexus has no legacy collection
and can consume this role from day one.

## Implementations and deployments, one contract

```
                        http://<host>:7997
                        /embeddings /rerank /classify /health /models
                                    |
        +---------------------------+---------------------------+
        |                                                       |
  native (this package)                              deploy/compose/
  sentence-transformers + fastembed                   - inference.yml (single container)
  uv run inference-platform                           - inference-split.yml (split pools)
  runs on the Mac host, one model copy
```

In single-service setups, exactly one listens on the port at a time. Consumers set
`INFERENCE_BASE_URL` and never learn which one is behind it.

In high-throughput production setups (`inference-split.yml`), the roles run in
isolated pools (`inference-embed:7997`, `inference-rerank:7998`,
`inference-classify:7999`), preventing cross-encoder head-of-line blocking on cache
embeddings. Consumers wire to them via `INFERENCE_EMBED_URL`,
`INFERENCE_RERANK_URL`, and `INFERENCE_NLI_URL`.

Requests are routed by **role** (`embed`, `sparse`, `rerank`, `relevance`, `nli`)
so that two roles sharing a route cannot be confused for one another. An explicit
`model` pin takes precedence and is mandatory when running Infinity, which routes
by model id only — set `INFERENCE_EMBED_MODEL` and friends in the gateway's
environment for a production deployment.

**Which to use:**

- **Mac M4 laptop** -> this package (`./dev/serve.sh`). No Linux VM, no lost GPU,
  ~2 GB resident instead of a duplicated stack per process.
- **Linux with a GPU, or anything under real concurrency** -> Infinity. Its value
  is continuous batching and pinned multi-model serving, neither of which a
  single-user laptop benefits from.

## Layout

```
pyproject.toml              dependency root (uv); no overlap with the gateway's Poetry env
uv.lock                     committed lock: `uv sync --frozen` is reproducible
models.lock.yaml            role -> model id, HF revision, dim, kind, label order
contract/INFERENCE_CONTRACT.md
src/inference_platform/
  api.py                    FastAPI app factory, routes, response shaping
  roles.py                  embedder / cross-encoder / sparse loaders + registry
  schemas.py                request models
  settings.py               lock path, role selection, device, cache dirs
  errors.py                 structured error shape
  __main__.py               console entry point (uvicorn, one worker)
tests/                      wire-contract tests against stand-in roles (no downloads)
dev/serve.sh                laptop runner (uv run inference-platform)
deploy/compose/inference.yml    Infinity, production
deploy/compose/inference-split.yml
deploy/.env.example         model pins for Infinity
tools/pin_models.py         records HF commit SHAs into models.lock.yaml
tools/bench.py              latency harness
evals/golden_pairs.jsonl    drift guard: known-good scores
evals/check.py
```

## Install and run (uv)

`uv` is the package manager here on purpose: it is a single binary, resolves and
locks in one step, and keeps this service's environment completely separate from
the gateway's Poetry environment. A torch bump for the engine must never be able to
move a gateway dependency.

```bash
cd inference-platform
uv sync                  # creates .venv, installs from uv.lock
./dev/serve.sh           # == uv run --frozen inference-platform
```

```bash
# what is loaded, and at which revisions
curl -s localhost:7997/models | python3 -m json.tool
curl -s localhost:7997/health | python3 -m json.tool

# dense + sparse in one call
curl -s localhost:7997/embeddings \
  -H 'content-type: application/json' \
  -d '{"input": ["how do i rotate an api key"], "dense": true, "sparse": true}' \
  | python3 -m json.tool
```

Then point the gateway at it:

```bash
# .env.local, when the gateway runs natively
INFERENCE_BASE_URL=http://localhost:7997

# when the gateway runs in OrbStack, inference on the host
INFERENCE_BASE_URL=http://host.docker.internal:7997
```

Day-to-day commands:

```bash
uv sync                 # after pulling a changed lock
uv lock                 # after editing pyproject.toml
uv run pytest           # wire-contract tests, no model downloads
uv run ruff check . && uv run ruff format --check .
uv run python evals/check.py     # drift guard against a running server
uv run python tools/pin_models.py --check
```

## Configuration

Everything is environment-driven, so the same package runs on a laptop, a VM, and
in a pod. Defaults are valid with no environment at all.

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `INFERENCE_HOST` | `127.0.0.1` | bind address (`0.0.0.0` in a container) |
| `INFERENCE_PORT` | `7997` | port |
| `INFERENCE_LOG_LEVEL` | `info` | uvicorn log level |
| `INFERENCE_DEVICE` | `auto` | `auto` (mps if available), `cpu`, `cuda`, `mps` |
| `INFERENCE_ROLES` | every role in the lock | comma list to load, e.g. `embed,sparse` |
| `INFERENCE_LOCK_PATH` | the lock in this checkout | override for a mounted config |
| `INFERENCE_MODEL_CACHE` | unset (library defaults) | one cache root for all weights; sets `HF_HOME` |
| `SPARSE_CACHE_PATH` | `$INFERENCE_MODEL_CACHE/fastembed` | fastembed cache directory |
| `SPARSE_MODEL_PATH` | unset | load the BM25 snapshot from an exact directory |
| `SPARSE_LOCAL_FILES_ONLY` | `false` | forbid network fetches (air-gapped deployments) |

Roles a consumer needs are the consumer's choice; this service has no opinion. If a
required role fails to load, `/health` returns `503` naming it, and the loading
process stays up so the failure is inspectable rather than a crash loop.

`INFERENCE_MODEL_CACHE` is unset by default on purpose: relocating the Hugging Face
cache on a machine that already has weights would silently re-download all of them.
Set it in containers and on VMs, where a warm volume is the difference between a
two-second and a two-minute start.

## Why this is separate from the gateway process

`DependencyContainer.initialize()` in the gateway eagerly builds the embedding
service, the reranker, and three cross-encoders (~2.2 GB resident). Every process
that calls it pays that cost, including every Celery prefork child. Consolidating
into one long-lived process makes that one copy instead of N, and takes ~2 GB of
weights out of `uvicorn --reload`'s restart loop.

## Measured on this laptop (M4, 16 GB)

Median of 15 runs, milliseconds, including the HTTP round trip (a bare loopback
`GET /health` costs 0.29 ms). "In-process" is `SentenceTransformer`/`CrossEncoder`
called directly, i.e. what the gateway did before the split.

| case | in-process, CPU | HTTP server, CPU | HTTP server, MPS |
| :--- | ---: | ---: | ---: |
| embed x1 | 8.2 | 5.0 | 8.1 |
| embed x8 | 6.8 | 7.2 | 8.5 |
| embed x32 | — | 14.7 | 11.0 |
| rerank x3 | 22.0 | 23.3 | 15.8 |
| rerank x16 | 48.0 | 53.9 | 33.7 |
| classify x1 | 23.4 | 20.8 | 14.9 |
| classify x8 | 36.5 | 38.1 | 24.6 |

Reading it:

- The network boundary costs roughly **0–6 ms** per call. It is not the problem.
- **MPS is worth 1.4–1.6×** on the cross-encoders (rerank, NLI) and roughly
  nothing — sometimes negative — on single-text embeddings, where kernel launch
  overhead outweighs the win. It pays off again at batch 32.
- The column that matters for the container question is **CPU vs MPS**: a Linux
  container on macOS cannot reach the MPS column. That, not container overhead,
  is the ~50% cost of containerizing on this machine.
- First-run noise is visible (`embed x1` beats in-process only because of thread
  warm-up ordering), so treat differences under ~2 ms as noise.

Reproduce with `uv run python tools/bench.py` against a running server.

## Pinning

`models.lock.yaml` is the single authority for "which model answers role X".
Consumers store an `embedding_version` alongside every vector; if a role's model
changes, vectors written under the old one must be treated as a different space.

```bash
uv run python tools/pin_models.py --write    # resolve "" revisions to commit SHAs
uv run python tools/pin_models.py --check    # fail if a pin is missing or drifted
```

Sparse roles are skipped by that tool: fastembed resolves and pins its own snapshot,
so a Hugging Face SHA in the lock would not describe what actually loads. The
tokenizer build is reported at runtime as `backend` on `/health`, and *that* string
is the version to record next to sparse vectors.

Never mutate a role's model in place. Serve the new one under a new id, dual-write,
then migrate. Qdrant collections are dimension-locked, so a silent model swap is a
silent cache corruption, not an error.
