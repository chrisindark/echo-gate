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
4. **Requests are routed by role.** Every request carries exactly one of
   `role` or `model`. `role` selects one of the roles in `models.lock.yaml`
   (`embed`, `rerank`, `relevance`, `nli`); `model` names a served model id
   explicitly and wins over `role`. The server rejects an unknown role or model
   rather than substituting one.

   This rule exists because two roles share the `/rerank` route: without it an
   unpinned rerank call is served by the engine's default reranker, so a
   consumer that wanted the relevance model silently gets the primary one.
   Engines that route by model id only (Infinity) require consumers to pin
   `model`; the role form is the native backend's convenience for callers that
   do not want to duplicate the lock file.
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
each role. Consumers verify this on startup so a version skew is visible, and so a
required role that failed to load stops the process instead of degrading silently.

This endpoint is optional for third-party engines. A `404`/`405` means "reachable
but capability unknown": the consumer logs a warning, skips role/dimension
verification for that endpoint, and relies on request-time degradation instead.
Infinity is such an engine — it serves `/embeddings`, `/rerank` and `/classify`
but has no introspection route, so pinning models is the only skew guard there.

## `POST /embeddings`

OpenAI-compatible request shape.

```json
{
  "role": "embed",
  "input": ["first text", "second text"],
  "dimensions": null
}
```

`model` may replace `role` to pin an explicit id (e.g.
`sentence-transformers/all-MiniLM-L6-v2`).

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
  "role": "rerank",
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
- The `relevance` role uses the same route with `"role": "relevance"`. It is a
  different model with a different calibration — do not assume score parity
  between roles, and always name the role you mean.

## `POST /classify`

NLI over premise/hypothesis pairs. Echo Gate sends `[user_prompt, cached_answer]`.

```json
{
  "role": "nli",
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

- Send `role` or `model` on every request; never rely on a server-side default.
  The default-role fallback exists for probes only, not for application traffic.
- Treat `503` (model not loaded) and connection failure identically: degrade to
  the LLM provider path. Semantic caching is never allowed to fail a request.
- Persist the model id + revision used to produce a vector alongside that vector.
  Echo Gate stores it as `embedding_version`; doc-nexus must do the same.
- Do not parse or depend on `score_raw` for production thresholds without
  recording the calibration in this repo.
