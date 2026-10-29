# Inference contract

The wire surface shared by Echo Gate, doc-nexus, and agent-forge. A consumer that
codes against this document works unchanged whether the native
`inference_platform` server or Infinity is answering.

The version is `1`. Additive request/response fields (for example `sparse`) do not
bump it: a consumer that predates the field keeps working, and a consumer that uses
it can detect support by the field's presence in the response. Bump the version
only for a breaking change, i.e. when an existing consumer must change code.

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
   (`embed`, `rerank`, `relevance`, `nli`, `zero-shot`, `sparse`); `model` names a
   served model id explicitly and wins over `role`. The server rejects an unknown
   role or model rather than substituting one.

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
    "embed":     {"model": "BAAI/bge-small-en-v1.5",              "kind": "embedder",      "loaded": true, "device": "mps", "dim": 384},
    "rerank":    {"model": "Alibaba-NLP/gte-reranker-modernbert-base", "kind": "cross-encoder", "loaded": true, "device": "mps", "dim": null},
    "relevance": {"model": "cross-encoder/ms-marco-MiniLM-L-6-v2", "kind": "cross-encoder", "loaded": true, "device": "mps", "dim": null},
    "nli":       {"model": "cross-encoder/nli-MiniLM2-L6-H768",    "kind": "cross-encoder", "loaded": true, "device": "mps", "labels": ["contradiction", "entailment", "neutral"]},
    "zero-shot": {"model": "MoritzLaurer/ModernBERT-base-zeroshot-v2.0", "kind": "cross-encoder", "loaded": true, "device": "mps", "labels": ["entailment", "not_entailment"]},
    "sparse":    {"model": "Qdrant/bm25",                         "kind": "sparse",        "loaded": true, "backend": "fastembed 0.9.1"}
  }
}
```

Model ids are whatever the deployment's `models.lock.yaml` declares — treat them as
opaque strings, and read `labels` rather than assuming a label order.

`kind` is `embedder` (dense vectors), `cross-encoder` (pairwise scores), or
`sparse` (lexical indices + weights), and tells a consumer what a role can answer
before it sends traffic. `backend` appears on sparse roles only: the tokenizer
build is part of the sparse vector space, so two services on different `fastembed`
versions produce incomparable indices.

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

OpenAI-compatible for dense, extended with an optional sparse branch in the same
call.

```json
{
  "role": "embed",
  "input": ["first text", "second text"],
  "dimensions": null,
  "dense": true,
  "sparse": false
}
```

| field | default | meaning |
| :--- | :--- | :--- |
| `role` / `model` | — | which dense role/model answers the `dense` branch |
| `input` | required | a string or an array of strings |
| `dimensions` | `null` | asserted dense length; a mismatch is `400 dimension_mismatch` |
| `dense` | `true` | compute dense vectors |
| `sparse` | `false` | compute sparse BM25 vectors in the same response |
| `sparse_model` | `null` | optional pin for the sparse branch (else the `sparse` role) |

Dense only — the default, and what every pre-existing consumer sends:

```json
{
  "object": "list",
  "model": "BAAI/bge-small-en-v1.5",
  "data": [
    {"object": "embedding", "index": 0, "embedding": [0.0123, -0.0456, "..."]},
    {"object": "embedding", "index": 1, "embedding": [0.0789, -0.0112, "..."]}
  ],
  "usage": {"prompt_tokens": 12, "total_tokens": 12}
}
```

Dense and sparse together (`"dense": true, "sparse": true`):

```json
{
  "object": "list",
  "model": "BAAI/bge-small-en-v1.5",
  "sparse_model": "Qdrant/bm25",
  "data": [
    {"object": "embedding", "index": 0, "embedding": [0.0123, -0.0456, "..."]}
  ],
  "sparse": [
    {"index": 0, "indices": [195839524, 1188412670], "values": [1.6786, 0.2501]}
  ],
  "usage": {"prompt_tokens": 7, "total_tokens": 7}
}
```

- **One hybrid request, not two.** Asking for both branches is a single round trip
  and a single tokenizer pass on the server. Two calls cost a second hop for no
  benefit.
- `sparse` entries preserve input order via `index`, exactly like `data`. `indices`
  are the tokenizer's vocabulary slots and `values` their BM25 weights;
  `len(indices) == len(values)`. There is no dimension: sparse vectors are compared
  by intersection, so the consumer stores them as Qdrant sparse vectors.
- **Sparse indices are a vector space of their own.** They come from a hashed
  tokenizer, so a collection must only ever hold vectors from one tokenizer build.
  `/health` reports `backend` on the sparse role; record it next to the vectors.
- `"dense": false` omits `model` and `data` entirely, so a sparse-only consumer
  never loads a dense model. `"dense": false, "sparse": false` is `400
  nothing_requested`.
- **Partial failure is explicit.** If dense succeeds and sparse raises, the
  response is `200` with `data` present, no `sparse` key, and `sparse_error`
  describing the failure — a hybrid consumer degrades to dense. If sparse was the
  only branch requested, the failure is `500 inference_failed` with
  `branch: "sparse"`, because there is nothing to return.

- `input` accepts a string or an array of strings. Arrays are the fast path (one
  batched forward pass); single strings are wrapped internally.
- Vector length equals the role's `dim` (384 for the default embed role) and is
  fixed by the loaded model. A mismatch means the consumer is talking to the wrong
  deployment.
- Vectors are L2-normalized when the role sets `normalize: true`. Cosine distance
  is scale-invariant, so this is safe either way, but consumers should not assume
  unit length when storing.
- Empty input array returns `{"object": "list", "data": [], "sparse": [], ...}`
  with `200`, not an error.

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
  "meta": {"model": "Alibaba-NLP/gte-reranker-modernbert-base", "billed_units": {"documents": 2}}
}
```

- `results` is sorted by descending `score`.
- `top_n` truncates after sorting. Omitted means "all documents".
- `documents` max 256 per request; larger batches get `413`.
- The `relevance` role uses the same route with `"role": "relevance"`. It is a
  different model with a different calibration — do not assume score parity
  between roles, and always name the role you mean.

## `POST /classify`

NLI on a role, in one of two modes. `mode` defaults to `"pairs"`, so a consumer
written before zero-shot existed is unaffected. Both modes require a role whose
lock declares `labels`: `pairs` defaults to `nli`, `zero-shot` to the `zero-shot`
role when the lock has one.

### `mode: "pairs"` (default)

Explicit premise/hypothesis pairs. Echo Gate sends `[user_prompt, cached_answer]`.

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
  "mode": "pairs",
  "role": "nli",
  "model": "cross-encoder/nli-MiniLM2-L6-H768",
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

### `mode: "zero-shot"`

Classification into caller-supplied labels with no training step: each label
becomes the hypothesis of an NLI pair, and the pair's entailment probability is
that label's score. This is how agent-forge triages mail without a generative
model — one small cross-encoder instead of a 3B-parameter LLM.

The mode prefers the dedicated `zero-shot` role when the lock declares one (a
binary entailment model, which is what these checkpoints are trained for) and
falls back to `nli` on engines that only serve that. Pin `role` explicitly to
choose; the response always states which model answered.

```json
{
  "role": "zero-shot",
  "mode": "zero-shot",
  "inputs": ["Please find attached the invoice for last month's subscription."],
  "labels": ["billing", "password reset", "shipping"],
  "hypothesis_template": "This text is about {}."
}
```

```json
{
  "mode": "zero-shot",
  "role": "zero-shot",
  "model": "MoritzLaurer/ModernBERT-base-zeroshot-v2.0",
  "labels": ["billing", "password reset", "shipping"],
  "entailment_label": "entailment",
  "hypothesis_template": "This text is about {}.",
  "results": [
    {
      "index": 0,
      "label": "billing",
      "score": 0.9740,
      "scores": {"billing": 0.9740, "password reset": 0.0002, "shipping": 0.0001},
      "scores_normalized": {"billing": 0.9997, "password reset": 0.0002, "shipping": 0.0001},
      "ranked": [
        {"label": "billing", "score": 0.9740, "score_normalized": 0.9997},
        {"label": "password reset", "score": 0.0002, "score_normalized": 0.0002},
        {"label": "shipping", "score": 0.0001, "score_normalized": 0.0001}
      ]
    }
  ]
}
```

`mode: "pairs"` responses carry the same `role` and `model` fields.

- **`scores` are independent, not a distribution.** Each label is a separate NLI
  decision, so several labels can each score `0.8`. Compare them, do not sum them.
- `scores_normalized` is a softmax *across labels* for consumers that must pick
  exactly one. Use it for ranking; never threshold on it.
- `ranked` is the same data sorted descending, so a consumer that only needs the
  winner does not re-sort. `label`/`score` are the top entry, for symmetry with
  `mode: "pairs"`.
- `hypothesis_template` must contain `{}`; the server substitutes each label into
  it. The default measured best of the phrasings tried on the shipped model
  (`This text is about {}.`).
- Limits: `labels` ≤ 32, `inputs` ≤ 64, and `inputs × labels` ≤ 256 (one forward
  pass per pair). Violations are `400` with `too_many_inputs`, `too_many_labels`,
  or `too_many_pairs`.
- Errors: `missing_inputs`, `missing_labels`, `empty_label`, `duplicate_labels`,
  `bad_hypothesis_template`, `no_entailment_label` (the role has no `entailment`
  class, so there is nothing to score).
- **Quality is a model question, not a contract question.** The shipped
  `zero-shot` model separates *topics* well (invoice → billing 0.97, password
  reset 0.998) and *intent* not at all (a phishing email ranks "account notice"
  at 0.97 and "phishing" at 0.001, because that is a judgement about the sender
  rather than something the text entails). Calibrate a threshold per label set,
  treat a low top score as "unknown" rather than forcing a choice, and prefer
  short noun-phrase labels. Changing the label set or the template changes every
  score: store both next to the classification you persist.
- Optional for third-party engines: Infinity serves only `mode: "pairs"`. A
  consumer must treat a `4xx` mentioning the mode (or `501 mode_not_supported`)
  as "unsupported here" and fall back to its generative path, exactly as it does
  for a `503`.

## Consumer obligations

- Send `role` or `model` on every request; never rely on a server-side default.
  The default-role fallback exists for probes only, not for application traffic.
- Treat `503` (model not loaded) and connection failure identically: degrade to
  the LLM provider path. Semantic caching is never allowed to fail a request.
- Persist the model id + revision used to produce a vector alongside that vector.
  Echo Gate stores it as `embedding_version`; doc-nexus must do the same. For
  sparse vectors, the *tokenizer build* is the version: store the sparse role's
  `backend` string from `/health` and never write two different builds into one
  collection.
- Do not parse or depend on `score_raw` for production thresholds without
  recording the calibration in this repo.
