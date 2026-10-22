# Resilient Client Guidance for Echo Gate

Echo Gate sits in-line as the gateway for your services, applications, and autonomous agents. When designed properly, calling services can achieve ultra-high availability by treating the gateway as degradable and resilient.

---

## 1. Gateway Degradation Contract

Every completion response from Echo Gate returns an `X-Gateway-Degradation` header with one of the following states:

| State | `X-Gateway-Degradation` Header | Description |
| --- | --- | --- |
| **FULL** | `none` | Normal operation: Served via cache hit or primary provider completion. |
| **CACHE_DEGRADED** | `cache_bypassed` | Fail-open cache: Redis or Qdrant had a transient blip; request was transparently forwarded to the provider without failing client calls. |
| **PROVIDER_FALLBACK** | `provider_fallback` | Upstream resilience: Primary provider failed (or its circuit breaker was open); request was transparently fulfilled by the configured fallback provider or next candidate in an alias. |
| **HARD_DOWN** | `hard_down` / HTTP 503 | Terminal failure: All candidate providers and fallbacks failed, or the circuit breaker is open with no remaining candidates. |

The header is present on both buffered JSON responses and streamed SSE responses
(`stream: true`). For streams it is resolved from the first chunk, so it is
available as soon as the response headers arrive — before you consume any body
chunks. When a request degrades twice, `provider_fallback` wins over
`cache_bypassed`, and a cache *hit* is always reported as `none`.

---

## 2. Request Timeout Budget Propagation

Agents and interactive interfaces often operate under strict latency budgets (e.g. 5 seconds total). Pass the total budget via the `X-Timeout-Budget-Ms` header:

```http
POST /api/v1/chat/completions HTTP/1.1
Host: gateway.example.com
Content-Type: application/json
X-API-Key: eg_your_api_key
X-Timeout-Budget-Ms: 4000
```

### What Echo Gate does with this header:
1. Calculates remaining budget across each lifecycle step (cache lookup, primary attempt, retries, and fallback).
2. Sets upstream provider HTTP timeouts dynamically to `min(configured_timeout, remaining_budget)`.
3. If the primary provider fails with only 200ms remaining, it aborts immediately with `504 Gateway Timeout` rather than spending seconds on an attempt doomed to exceed your deadline.

---

## 3. Intelligent Model Aliasing

Instead of hardcoding provider-specific models like `openai/gpt-4o-mini` or `groq/openai/gpt-oss-20b`, send logical aliases:

```json
{
  "model": "fast",
  "messages": [{"role": "user", "content": "Summarize this ticket"}]
}
```

Pre-configured aliases:
- `"fast"`: Groq -> Gemini Flash -> OpenAI GPT-4o-mini.
- `"cheap"`: OpenRouter free models -> Gemini Flash-Lite -> OpenAI GPT-4o-mini.
- `"quality"`: Anthropic Claude Sonnet -> Gemini Flash -> OpenAI GPT-4o-mini.

If the top-ranked provider's circuit breaker is open or its bulkhead is saturated, Echo Gate automatically skips to the next candidate with zero latency penalty.

---

## 4. Structured Provider Errors

Upstream provider failures return structured, actionable error codes:

```json
{
  "error": {
    "message": "Provider 'gemini' rate limit reached: ...",
    "type": "invalid_request_error",
    "code": 429,
    "error_code": "provider_rate_limit",
    "provider": "gemini"
  }
}
```

Standard error codes:
- `provider_content_filter`: Prompt rejected by upstream safety filters (do not retry without modifying the prompt).
- `provider_rate_limit`: Upstream rate limit exceeded (retry with exponential backoff).
- `provider_quota_exceeded`: Provider account quota exhausted (switch provider or alert operator).
- `provider_auth_error`: Invalid upstream credentials.
- `provider_unavailable`: Upstream 5xx or connection error.
- `provider_timeout`: Upstream deadline exceeded.

---

## 5. Rate-Limit Budget Headers

The gateway does not make you discover your quota by being rejected. Every
guarded response — buffered or streamed — advertises the remaining budget for
your bucket:

| Header | Meaning |
| --- | --- |
| `X-RateLimit-Limit-Requests` | RPM ceiling applied to this request. |
| `X-RateLimit-Remaining-Requests` | Requests left in the current 60s window (this one already counted). |
| `X-RateLimit-Reset-Requests` | Seconds until the request window resets. |
| `X-RateLimit-Limit-Tokens` | TPM ceiling applied to this request. |
| `X-RateLimit-Remaining-Tokens` | Tokens left in the current window. |
| `X-RateLimit-Reset-Tokens` | Seconds until the token window resets. |
| `Retry-After` | Present on retryable refusals only (`429`, `503`): seconds to wait before retrying. |

Practical rules for agents:

1. **Self-throttle on `Remaining-*`.** When `X-RateLimit-Remaining-Requests`
   approaches zero, pace yourself for `X-RateLimit-Reset-Requests` seconds
   instead of pushing until a `429`. Token budgets move in bigger steps, so
   `Remaining-Tokens` is the better signal for long prompts.
2. **Honour `Retry-After` exactly.** It is the gateway's own estimate of when
   retrying becomes worthwhile, and it is sent on every retryable refusal:
   quota (`429`), bulkhead shed (`429`), open circuit breaker (`503`), and total
   upstream failure (`503` where no candidate answered, or a stream broke). For
   quota it is the live Redis window TTL, so waiting that long clears the
   counter; for upstream failures it is the longest remaining breaker recovery
   window (or `RETRY_AFTER_DEFAULT_SECONDS`, default 5, when no breaker is open).
   Non-retryable failures (`400`/`401`/`402`/`403`) never carry it — retrying
   them cannot succeed.
3. **Expect jitter between replicas.** Each gateway process reads the same Redis
   counters, but a response produced while another request is in flight may be a
   little stale. Treat the values as advisory, not transactional.
4. **Retries consume budget.** A retried request is another RPM, so a naive
   `tenacity` loop can turn a momentary throttle into a sustained one. Budget the
   retries (see the blueprint below) and prefer fewer, longer waits.

```python
def budget_from_headers(response: httpx.Response) -> tuple[int, float]:
    remaining = int(response.headers.get("X-RateLimit-Remaining-Requests", "1"))
    reset_s = float(response.headers.get("X-RateLimit-Reset-Requests", "60"))
    return remaining, reset_s

# Pause for the window instead of hammering the gateway into a 429.
remaining, reset_s = budget_from_headers(response)
if remaining <= 1:
    time.sleep(reset_s)
```

## 6. Client Retry & Fallback Blueprint

Here is a recommended Python client pattern using `tenacity` and `httpx`:

```python
import os
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    return False

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=8),
    retry=retry_if_exception(is_retryable),
    reraise=True,
)
def call_echo_gate(messages, timeout_budget_ms=5000):
    try:
        response = httpx.post(
            "https://gateway.example.com/api/v1/chat/completions",
            headers={
                "X-API-Key": os.environ["ECHO_GATE_API_KEY"],
                "X-Timeout-Budget-Ms": str(timeout_budget_ms),
            },
            json={
                "model": "fast",
                "messages": messages,
            },
            timeout=timeout_budget_ms / 1000.0,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        # Client-side emergency bypass: call direct provider if gateway is unavailable
        if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 503:
            # Check Retry-After header
            retry_after = e.response.headers.get("Retry-After", "5")
            print(f"Gateway is degraded (Retry-After: {retry_after}s); engaging bypass...")
        raise e
```
