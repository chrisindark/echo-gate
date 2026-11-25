# app/core/pricing.py

# Prices are per 1 token
PRICING_CONFIG = {
    "openai": {
        # GPT-6
        "gpt-6.2-astra": {"prompt": 8.00 / 1_000_000, "completion": 40.00 / 1_000_000},
        "gpt-6.2-sol": {"prompt": 2.00 / 1_000_000, "completion": 10.00 / 1_000_000},
        "gpt-6.1-sol": {"prompt": 2.00 / 1_000_000, "completion": 10.00 / 1_000_000},
        "gpt-6-luna": {"prompt": 0.10 / 1_000_000, "completion": 0.50 / 1_000_000},
        # GPT-5.x
        "gpt-5.4": {"prompt": 2.50 / 1_000_000, "completion": 15.00 / 1_000_000},
        "gpt-5.4-mini": {"prompt": 0.75 / 1_000_000, "completion": 4.50 / 1_000_000},
        "gpt-5.4-nano": {"prompt": 0.20 / 1_000_000, "completion": 1.25 / 1_000_000},
        "gpt-5.2": {"prompt": 1.75 / 1_000_000, "completion": 14.00 / 1_000_000},
        "gpt-5": {"prompt": 1.25 / 1_000_000, "completion": 10.00 / 1_000_000},
        "gpt-5-mini": {"prompt": 0.25 / 1_000_000, "completion": 2.00 / 1_000_000},
        "gpt-5-nano": {"prompt": 0.05 / 1_000_000, "completion": 0.40 / 1_000_000},
        # GPT-4.1 & GPT-4o
        "gpt-4.1": {"prompt": 2.00 / 1_000_000, "completion": 8.00 / 1_000_000},
        "gpt-4.1-mini": {"prompt": 0.40 / 1_000_000, "completion": 1.60 / 1_000_000},
        "gpt-4.1-nano": {"prompt": 0.10 / 1_000_000, "completion": 0.40 / 1_000_000},
        "gpt-4o": {"prompt": 2.50 / 1_000_000, "completion": 10.00 / 1_000_000},
        "gpt-4o-mini": {"prompt": 0.150 / 1_000_000, "completion": 0.600 / 1_000_000},
        # Reasoning o-series
        "o3": {"prompt": 2.00 / 1_000_000, "completion": 8.00 / 1_000_000},
        "o4-mini": {"prompt": 1.10 / 1_000_000, "completion": 4.40 / 1_000_000},
        "o3-mini": {"prompt": 1.10 / 1_000_000, "completion": 4.40 / 1_000_000},
    },
    "google-genai": {
        # Gemini 3.x
        "gemini-3.8-flash": {
            "prompt": 0.75 / 1_000_000,
            "completion": 3.75 / 1_000_000,
        },
        "gemini-3.5-flash-lite": {
            "prompt": 1.25 / 1_000_000,
            "completion": 5.00 / 1_000_000,
        },
        "gemini-3.1-pro-preview": {
            "prompt": 2.00 / 1_000_000,
            "completion": 12.00 / 1_000_000,
        },
        "gemini-3.1-pro": {
            "prompt": 2.00 / 1_000_000,
            "completion": 12.00 / 1_000_000,
        },
        "gemini-3.1-flash-lite": {
            "prompt": 0.25 / 1_000_000,
            "completion": 1.50 / 1_000_000,
        },
        # Gemini 2.5
        "gemini-2.5-pro": {
            "prompt": 1.25 / 1_000_000,
            "completion": 10.00 / 1_000_000,
        },
        "gemini-2.5-flash": {
            "prompt": 0.30 / 1_000_000,
            "completion": 2.50 / 1_000_000,
        },
        "gemini-2.5-flash-lite": {
            "prompt": 0.10 / 1_000_000,
            "completion": 0.40 / 1_000_000,
        },
        # Gemini 2.0
        "gemini-2.0-flash": {
            "prompt": 0.10 / 1_000_000,
            "completion": 0.40 / 1_000_000,
        },
        "gemini-2.0-flash-lite": {
            "prompt": 0.075 / 1_000_000,
            "completion": 0.300 / 1_000_000,
        },
        # Gemini 1.5
        "gemini-1.5-pro": {
            "prompt": 1.25 / 1_000_000,
            "completion": 5.00 / 1_000_000,
        },
        "gemini-1.5-flash": {
            "prompt": 0.075 / 1_000_000,
            "completion": 0.300 / 1_000_000,
        },
        "default": {
            "prompt": 0.30 / 1_000_000,
            "completion": 2.50 / 1_000_000,
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
        # Claude 5.x
        "claude-fable-5-2": {
            "prompt": 8.00 / 1_000_000,
            "completion": 40.00 / 1_000_000,
        },
        "claude-opus-5-5": {
            "prompt": 4.00 / 1_000_000,
            "completion": 20.00 / 1_000_000,
        },
        "claude-sonnet-5-5": {
            "prompt": 2.00 / 1_000_000,
            "completion": 10.00 / 1_000_000,
        },
        "claude-haiku-5-5": {
            "prompt": 0.10 / 1_000_000,
            "completion": 0.50 / 1_000_000,
        },
        # Claude 4.6
        "claude-opus-4-6": {
            "prompt": 5.00 / 1_000_000,
            "completion": 25.00 / 1_000_000,
        },
        "claude-sonnet-4-6": {
            "prompt": 3.00 / 1_000_000,
            "completion": 15.00 / 1_000_000,
        },
        # Claude 4.5
        "claude-opus-4-5-20251101": {
            "prompt": 5.00 / 1_000_000,
            "completion": 25.00 / 1_000_000,
        },
        "claude-sonnet-4-5-20250929": {
            "prompt": 3.00 / 1_000_000,
            "completion": 15.00 / 1_000_000,
        },
        "claude-haiku-4-5-20251001": {
            "prompt": 1.00 / 1_000_000,
            "completion": 5.00 / 1_000_000,
        },
        # Claude 4.x
        "claude-opus-4-1-20250805": {
            "prompt": 15.00 / 1_000_000,
            "completion": 75.00 / 1_000_000,
        },
        "claude-sonnet-4-20250514": {
            "prompt": 3.00 / 1_000_000,
            "completion": 15.00 / 1_000_000,
        },
        "claude-opus-4-20250514": {
            "prompt": 15.00 / 1_000_000,
            "completion": 75.00 / 1_000_000,
        },
        # Claude 3.x
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
PRICING_CONFIG["anthropic"] = PRICING_CONFIG["claude"]


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
