# inference-platform

Shared model-serving layer for Echo Gate, doc-nexus, and agent-forge.

It is **not** an application. It owns no business logic, no cache decisions, and no
data. It holds model weights in memory and answers four HTTP routes. Consumers
import nothing from here except the wire contract.

## What is shared, and what is not

| Shared here | Stays in the consumer |
| :--- | :--- |
| Dense embeddings (`/embeddings`) | Cache keys, thresholds, retention policy |
| Cross-encoder rerank (`/rerank`) | Which candidates to send |
| NLI / contradiction (`/classify`) | Whether a contradiction blocks a cache hit |
| — | BM25 sparse vectors (tokenizer fidelity, see below) |
| — | Entity extraction (pure regex, microseconds) |
| — | Qdrant, Redis, Postgres (separate per app) |

**BM25 is deliberately not shared.** `fastembed`'s `Qdrant/bm25` is a tokenizer
pass costing ~1 ms and a few MB. Serving it remotely would add a hop and, worse,
risk tokenizer skew: if the server's BM25 differs at all, previously written
Qdrant sparse vectors stop matching. Each app keeps its own copy.

## Implementations and deployments, one contract

```
                        http://<host>:7997
                        /embeddings /rerank /classify /health
                                    |
        +---------------------------+---------------------------+
        |                                                       |
  dev/native_server.py                                 deploy/compose/
  sentence-transformers + CrossEncoder                 - inference.yml (single container)
  runs on the Mac host, one model copy                 - inference-split.yml (split pools)
```

In single-service setups, exactly one listens on the port at a time. Consumers set
`INFERENCE_BASE_URL` and never learn which one is behind it.

In high-throughput production setups (`inference-split.yml`), the roles run in
isolated pools (`inference-embed:7997`, `inference-rerank:7998`, `inference-classify:7999`),
preventing cross-encoder head-of-line blocking on cache embeddings. Consumers wire to
them via `INFERENCE_EMBED_URL`, `INFERENCE_RERANK_URL`, and `INFERENCE_NLI_URL`.

Requests are routed by **role** (`embed`, `rerank`, `relevance`, `nli`) so that
two roles sharing a route cannot be confused for one another. An explicit
`model` pin takes precedence and is mandatory when running Infinity, which routes
by model id only — set `INFERENCE_EMBED_MODEL` and friends in the gateway's
environment for a production deployment.

**Which to use:**

- **Mac M4 laptop** -> `native_server.py`. No Linux VM, no lost GPU, ~2 GB
  resident instead of a duplicated stack per process.
- **Linux with a GPU, or anything under real concurrency** -> Infinity. Its value
  is continuous batching and pinned multi-model serving, neither of which a
  single-user laptop benefits from.

## Layout

```
models.lock.yaml            role -> model id, HF revision, dim, label order
contract/INFERENCE_CONTRACT.md
dev/native_server.py        laptop implementation
dev/requirements.txt
dev/serve.sh
deploy/compose/inference.yml    Infinity, production
deploy/.env.example             model pins for Infinity
tools/pin_models.py         records HF commit SHAs into models.lock.yaml
evals/golden_pairs.jsonl    drift guard: known-good scores
evals/check.py
```

## Run on the laptop

```bash
cd inference-platform
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r dev/requirements.txt
./dev/serve.sh                     # http://localhost:7997

curl -s localhost:7997/health | python3 -m json.tool
```

Then point the gateway at it:

```bash
# .env.local, when the gateway runs natively
INFERENCE_BASE_URL=http://localhost:7997

# when the gateway runs in OrbStack, inference on the host
INFERENCE_BASE_URL=http://host.docker.internal:7997
```

## Why this is separate from the gateway process

`DependencyContainer.initialize()` in the gateway eagerly builds the embedding
service, the reranker, and three cross-encoders (~2.2 GB resident). Every process
that calls it pays that cost, including every Celery prefork child. Consolidating
into one long-lived process makes that one copy instead of N, and takes ~2 GB of
weights out of `uvicorn --reload`'s restart loop.

## Measured on this laptop (M4, 16 GB)

Median of 15 runs, milliseconds, including the HTTP round trip (a bare loopback
`GET /health` costs 0.29 ms). "In-process" is `SentenceTransformer`/`CrossEncoder`
called directly, i.e. what the gateway does today.

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

Reproduce with `python tools/bench.py` against a running server.

## Pinning

`models.lock.yaml` is the single authority for "which model answers role X".
Consumers store an `embedding_version` alongside every vector; if a role's model
changes, vectors written under the old one must be treated as a different space.

```bash
python tools/pin_models.py --write    # resolve "" revisions to commit SHAs
python tools/pin_models.py --check    # fail if a pinned revision went missing
```

Never mutate a role's model in place. Serve the new one under a new id, dual-write,
then migrate. Qdrant collections are dimension-locked, so a silent model swap is a
silent cache corruption, not an error.
