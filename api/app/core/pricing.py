# app/core/pricing.py

# Prices are per 1 token
PRICING_CONFIG = {
    "openai": {
        "gpt-4o-mini": {"prompt": 0.150 / 1_000_000, "completion": 0.600 / 1_000_000},
        "gpt-4o": {"prompt": 5.0 / 1_000_000, "completion": 15.0 / 1_000_000},
    },
    "google-genai": {
        "gemini-3.1-flash-lite": {
            "prompt": 0.075 / 1_000_000,
            "completion": 0.300 / 1_000_000,
        },
        "gemini-3.5-flash-lite": {
            "prompt": 1.25 / 1_000_000,
            "completion": 5.00 / 1_000_000,
        },
        "gemini-3.8-flash": {
            "prompt": 0.075 / 1_000_000,
            "completion": 0.300 / 1_000_000,
        },
    },
    "ollama": {
        # Local models are free
        "default": {"prompt": 0.0, "completion": 0.0}
    },
    "mock": {"default": {"prompt": 0.0, "completion": 0.0}},
    "groq": {"default": {"prompt": 0.0, "completion": 0.0}},
    "openrouter": {
        "qwen/qwen3.8-27b:free": {"prompt": 0.0, "completion": 0.0},
        "google/gemma-4-26b-a4b-it:free": {"prompt": 0.0, "completion": 0.0},
        "google/gemma-4-31b-it:free": {"prompt": 0.0, "completion": 0.0},
        "default": {"prompt": 0.0, "completion": 0.0},
    },
    "claude": {
        "claude-3-7-sonnet-20250219": {
            "prompt": 3.0 / 1_000_000,
            "completion": 15.0 / 1_000_000,
        },
        "claude-3-5-sonnet-20241022": {
            "prompt": 3.0 / 1_000_000,
            "completion": 15.0 / 1_000_000,
        },
        "claude-3-5-haiku-20241022": {
            "prompt": 0.80 / 1_000_000,
            "completion": 4.0 / 1_000_000,
        },
        "claude-3-opus-20240229": {
            "prompt": 15.0 / 1_000_000,
            "completion": 75.0 / 1_000_000,
        },
        "claude-3-haiku-20240307": {
            "prompt": 0.25 / 1_000_000,
            "completion": 1.25 / 1_000_000,
        },
        "default": {"prompt": 3.0 / 1_000_000, "completion": 15.0 / 1_000_000},
    },
    "anthropic": {
        "claude-3-7-sonnet-20250219": {
            "prompt": 3.0 / 1_000_000,
            "completion": 15.0 / 1_000_000,
        },
        "claude-3-5-sonnet-20241022": {
            "prompt": 3.0 / 1_000_000,
            "completion": 15.0 / 1_000_000,
        },
        "claude-3-5-haiku-20241022": {
            "prompt": 0.80 / 1_000_000,
            "completion": 4.0 / 1_000_000,
        },
        "claude-3-opus-20240229": {
            "prompt": 15.0 / 1_000_000,
            "completion": 75.0 / 1_000_000,
        },
        "claude-3-haiku-20240307": {
            "prompt": 0.25 / 1_000_000,
            "completion": 1.25 / 1_000_000,
        },
        "default": {"prompt": 3.0 / 1_000_000, "completion": 15.0 / 1_000_000},
    },
}

# `gemini` and `google-genai` are the same provider under both accepted service
# names (`LlmProviderService.generate_completion` routes both to the Gemini
# adapter), so a price lookup must not depend on which spelling the caller used.
PRICING_CONFIG["gemini"] = PRICING_CONFIG["google-genai"]


# Upstream prompt-cache multipliers applied to the provider's input price.
# These are *provider-native* cache rates, orthogonal to Echo Gate's semantic
# cache: OpenAI implicit caching bills cache reads at 0.5x, Anthropic charges
# 0.1x for cache reads and 1.25x for cache writes, and Gemini implicit caching
# bills cache reads at 0.25x.
CACHE_TOKEN_MULTIPLIERS: dict[str, tuple[float, float]] = {
    "openai": (0.5, 1.0),
    "claude": (0.1, 1.25),
    "anthropic": (0.1, 1.25),
    "gemini": (0.25, 1.0),
    "google-genai": (0.25, 1.0),
}
DEFAULT_CACHE_TOKEN_MULTIPLIERS = (1.0, 1.0)


def get_cache_token_multipliers(provider: str) -> tuple[float, float]:
    """Return ``(cache_read_multiplier, cache_write_multiplier)`` for a provider."""
    return CACHE_TOKEN_MULTIPLIERS.get(
        provider.lower(), DEFAULT_CACHE_TOKEN_MULTIPLIERS
    )


def calculate_cost(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Price a completion, including provider-native prompt-cache discounts.

    ``prompt_tokens`` is total input (cached portions included). Non-cached
    input is billed at the normal prompt rate, cache reads at the provider's
    read multiplier, and cache writes at its write multiplier.
    """
    prompt_price, completion_price = get_model_prices(provider, model)
    read_multiplier, write_multiplier = get_cache_token_multipliers(provider)

    non_cached = max(prompt_tokens - cache_read_tokens - cache_creation_tokens, 0)
    input_cost = (
        non_cached * prompt_price
        + cache_read_tokens * prompt_price * read_multiplier
        + cache_creation_tokens * prompt_price * write_multiplier
    )
    return input_cost + completion_tokens * completion_price


def get_model_prices(provider: str, model: str) -> tuple[float, float]:
    """
    Returns (prompt_price_per_token, completion_price_per_token).
    """
    provider_prices = PRICING_CONFIG.get(provider.lower(), {})

    if not provider_prices:
        return 0.0, 0.0

    model_prices = provider_prices.get(model, provider_prices.get("default"))

    if not model_prices:
        return 0.0, 0.0

    return model_prices["prompt"], model_prices["completion"]
