---
name: provider-adapter
description: SOP for adding, updating, or debugging LLM provider adapters in Echo Gate (Gemini, Ollama, OpenAI, Groq, OpenRouter). Use when working on LLM routing, streaming responses, or provider fallbacks.
---

# LLM Provider Adapters in Echo Gate

Echo Gate accepts OpenAI-compatible `chat/completions` requests and routes them dynamically across upstream LLM providers while enforcing timeouts, retries, and fallback circuits.

## 1. Where Things Live
- Dispatch façade: `api/app/modules/llm/llm_provider_service.py`
- Provider implementations: `api/app/modules/llm/providers/`
  - `gemini.py` (`google-genai` SDK)
  - `openai.py` (`openai` SDK)
  - `groq.py` (`groq` SDK)
  - `openrouter.py` (OpenAI-compatible)
  - `ollama.py` (Local HTTP)
  - `openai_compatible.py` (Shared streaming event loop)
  - `mock.py` (Unit testing provider)

## 2. Invariants & Implementation Rules
- **Streaming Contract**: Streaming responses must emit standardized Server-Sent Events (SSE) compliant with OpenAI's `data: {...}\n\ndata: [DONE]\n\n` protocol.
- **Async I/O**: All network/provider calls must be fully asynchronous. Use `app/core/retry.py` for exponential backoff on retryable HTTP status codes.
- **Fail-Open Fallback**: Provider failure or consecutive timeouts trip the per-provider circuit breaker in `app/core/circuit_breaker.py` and trigger fallback provider switchover.
- **Model Whitelist**: Keep `ALLOWED_MODELS` in `api/app/modules/chat/chat_schema.py` in sync with any newly supported provider models. (Frontend `web/src/app/page.tsx` also mirrors this map).

## 3. Verification
- Test provider logic: `poetry run pytest api/app/modules/llm/tests/`
- Test router resilience & fallback: `poetry run pytest api/app/modules/llm/tests/test_router_resilience.py`
