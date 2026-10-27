# Inference contract

The wire surface shared by Echo Gate, doc-nexus, and agent-forge. A consumer that
codes against this document works unchanged whether `dev/native_server.py` or
Infinity is answering.

Base URL: `$INFERENCE_BASE_URL` (default port `7997`). All routes are `POST` except
the probes. No authentication: the service is expected on a private network only.

## Rules that make the two implementations interchangeable

1. **Scores are normalized, never raw logits.** Cross-encoders emit unbounded
   logits; a sigmoid in one implementation and a softmax in another would break
   every threshold. `score` is always `sigmoid(logit)` in `[0, 1]`.
2. **Raw values are also returned** as `score_raw` for calibration work. New code
   must read `score`; `score_raw` exists so existing thresholds can be re-derived
   without a second migration.
3. **NLI returns labeled probabilities.** Consumers index by `labels`, never by
   position.
4. **Model selection is explicit.** Every request carries `model`; the server
   rejects a model it was not started with rather than silently substituting.
5. **Batch in, batch out.** `input`, `documents`, and `pairs` accept arrays and
   the response preserves input order with an `index` field.
6. **Errors are structured.** `4xx` = caller error, `503` = model not loaded,
   `500` = inference failure. Consumers must treat `503` as "fall back to the
   LLM provider", never as a client error.

## Health probes

### `GET /health`

```json
{
  "status": "ok",
  "version": "1",
  "roles": {
    "embed":  {"model": "sentence-transformers/all-MiniLM-L6-v2", "loaded": true, "device": "mps"},
    "rerank": {"model": "BAAI/bge-reranker-base",                "loaded": true, "device": "mps"},
    "nli":    {"model": "cross-encoder/nli-deberta-v3-small",    "loaded": true, "device": "mps"}
  }
}
```

`status` is `ok` only when every role the server was asked to load is ready.
Returns `503` with `status: "degraded"` otherwise, listing `unloaded` roles.

### `GET /models`

Returns the effective `models.lock.yaml` contents plus the resolved revision for
each role. Consumers log this on startup so a version skew is visible in logs.

## `POST /embeddings`

OpenAI-compatible request shape.

```json
{
  "model": "sentence-transformers/all-MiniLM-L6-v2",
  "input": ["first text", "second text"],
  "dimensions": null
}
```

```json
{
  "object": "list",
  "model": "sentence-transformers/all-MiniLM-L6-v2",
  "data": [
    {"object": "embedding", "index": 0, "embedding": [0.0123, -0.0456, "..."]},
    {"object": "embedding", "index": 1, "embedding": [0.0789, -0.0112, "..."]}
  ],
  "usage": {"prompt_tokens": 12, "total_tokens": 12}
}
```

- `input` accepts a string or an array of strings. Arrays are the fast path
  (one batched forward pass); single strings are wrapped internally.
- Vector length equals the role's `dim` (384 for the default embed role) and is
  fixed by the loaded model. A mismatch means the consumer is talking to the
  wrong deployment.
- Vectors are L2-normalized when the role sets `normalize: true`. Cosine
  distance is scale-invariant, so this is safe either way, but consumers should
  not assume unit length when storing.
- Empty input array returns `{"object": "list", "data": [], ...}` with `200`,
  not an error.

## `POST /rerank`

Cohere-compatible, extended with the raw score.

```json
{
  "model": "BAAI/bge-reranker-base",
  "query": "how do I rotate an api key",
  "documents": ["Rotating API keys is done in the console.", "The weather is mild."],
  "top_n": 2,
  "return_documents": false
}
```

```json
{
  "results": [
    {"index": 0, "score": 0.9412, "score_raw": 2.7710},
    {"index": 1, "score": 0.0331, "score_raw": -3.3760}
  ],
  "meta": {"model": "BAAI/bge-reranker-base", "billed_units": {"documents": 2}}
}
```

- `results` is sorted by descending `score`.
- `top_n` truncates after sorting. Omitted means "all documents".
- `documents` max 256 per request; larger batches get `413`.
- The `relevance` role uses the same route with a different `model`. It is a
  different model with a different calibration — do not assume score parity
  between roles.

## `POST /classify`

NLI over premise/hypothesis pairs. Echo Gate sends `[user_prompt, cached_answer]`.

```json
{
  "model": "cross-encoder/nli-deberta-v3-small",
  "pairs": [
    ["What is the refund window?", "Refunds are accepted within 30 days."]
  ]
}
```

```json
{
  "labels": ["contradiction", "entailment", "neutral"],
  "results": [
    {
      "index": 0,
      "probabilities": {"contradiction": 0.0231, "entailment": 0.9542, "neutral": 0.0227},
      "label": "entailment",
      "logits": [-3.7401, 0.2504, -3.7802]
    }
  ]
}
```

- `probabilities` sums to 1 across the declared `labels`.
- `label` is the argmax, provided for convenience; consumers that gate on a
  specific class must read `probabilities[label]` so a label-order change is a
  visible failure rather than a silent inversion.
- `logits` are unnormalized and returned for debugging only.

## Consumer obligations

- Send `model` on every request; never rely on a server-side default.
- Treat `503` (model not loaded) and connection failure identically: degrade to
  the LLM provider path. Semantic caching is never allowed to fail a request.
- Persist the model id + revision used to produce a vector alongside that vector.
  Echo Gate stores it as `embedding_version`; doc-nexus must do the same.
- Do not parse or depend on `score_raw` for production thresholds without
  recording the calibration in this repo.
