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
}


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
