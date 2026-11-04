"""Per-provider LLM adapters.

Each module owns exactly one upstream and is mixed into
``LlmProviderService`` (``app.modules.llm.llm_provider_service``), which keeps
credential/client construction and the ``service_name`` dispatch. Adding a
provider means adding a module here and one dispatch branch there.
"""

from app.modules.llm.providers.claude import ClaudeProvider
from app.modules.llm.providers.gemini import GeminiProvider
from app.modules.llm.providers.groq import GroqProvider
from app.modules.llm.providers.mock import MockProvider
from app.modules.llm.providers.ollama import OllamaProvider
from app.modules.llm.providers.openai import OpenAIProvider
from app.modules.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.modules.llm.providers.openrouter import OpenRouterProvider

__all__ = [
    "ClaudeProvider",
    "GeminiProvider",
    "GroqProvider",
    "MockProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
]
