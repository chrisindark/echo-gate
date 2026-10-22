import json
import logging
from dataclasses import dataclass

from app.core.config import config

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_MODELS: dict[str, list[str]] = {
    "ollama": [
        "qwen3:8b",
        "qwen2.5-coder:14b",
        "qwen2.5-coder:1.5b",
        "qwen2.5-coder:3b",
        "qwen2.5-coder:7b",
    ],
    "gemini": [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ],
    "google-genai": [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ],
    "openai": [
        "gpt-4o-mini",
    ],
    "groq": ["openai/gpt-oss-20b", "openai/gpt-oss-safeguard-20b"],
    "openrouter": [
        "qwen/qwen3.8-27b:free",
        "google/gemma-4-26b-a4b-it:free",
        "google/gemma-4-31b-it:free",
    ],
    "claude": [
        "claude-3-7-sonnet-20250219",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
        "claude-3-haiku-20240307",
    ],
    "anthropic": [
        "claude-3-7-sonnet-20250219",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
        "claude-3-haiku-20240307",
    ],
}

DEFAULT_MODEL_ALIASES: dict[str, list[dict[str, str]]] = {
    "fast": [
        {"service": "groq", "model": "openai/gpt-oss-20b"},
        {"service": "google-genai", "model": "gemini-3.1-flash-lite"},
        {"service": "openai", "model": "gpt-4o-mini"},
    ],
    "cheap": [
        {"service": "openrouter", "model": "qwen/qwen3.8-27b:free"},
        {"service": "google-genai", "model": "gemini-3.1-flash-lite"},
        {"service": "openai", "model": "gpt-4o-mini"},
    ],
    "quality": [
        {"service": "claude", "model": "claude-3-7-sonnet-20250219"},
        {"service": "google-genai", "model": "gemini-3.8-flash"},
        {"service": "openai", "model": "gpt-4o-mini"},
    ],
}


@dataclass(frozen=True)
class ModelTarget:
    """Resolved candidate target containing provider and model name."""

    service: str
    model: str


class DynamicModelRegistry:
    """Dynamic, config-driven model registry and intelligent alias mapper.

    Allows runtime addition of allowed models and intelligent aliases without
    code deployment or gateway redeployment.
    """

    def __init__(self) -> None:
        self._models: dict[str, set[str]] = {}
        self._disabled_models: set[tuple[str, str]] = set()
        self._aliases: dict[str, list[ModelTarget]] = {}
        self.reload()

    def reload(self) -> None:
        """Reload models and aliases from defaults and environment config."""
        self._models = {
            svc: set(models) for svc, models in DEFAULT_ALLOWED_MODELS.items()
        }

        # Override/extend from ALLOWED_MODELS_JSON
        env_models = getattr(config, "ALLOWED_MODELS_JSON", None)
        if env_models:
            try:
                parsed = json.loads(env_models)
                if isinstance(parsed, dict):
                    for svc, models in parsed.items():
                        if isinstance(models, list):
                            self._models.setdefault(svc, set()).update(models)
            except Exception as e:
                logger.error("Failed to parse ALLOWED_MODELS_JSON: %s", e)

        # Load aliases
        self._aliases = {}
        for alias, targets in DEFAULT_MODEL_ALIASES.items():
            self._aliases[alias] = [
                ModelTarget(service=t["service"], model=t["model"]) for t in targets
            ]

        env_aliases = getattr(config, "MODEL_ALIASES_JSON", None)
        if env_aliases:
            try:
                parsed_aliases = json.loads(env_aliases)
                if isinstance(parsed_aliases, dict):
                    for alias, targets in parsed_aliases.items():
                        if isinstance(targets, list):
                            self._aliases[alias] = [
                                ModelTarget(
                                    service=t["service"],
                                    model=t["model"],
                                )
                                for t in targets
                                if isinstance(t, dict)
                                and "service" in t
                                and "model" in t
                            ]
            except Exception as e:
                logger.error("Failed to parse MODEL_ALIASES_JSON: %s", e)

    def get_allowed_models(self, service: str | None = None) -> list[str]:
        if service is None:
            all_models = set()
            for models in self._models.values():
                all_models.update(models)
            return sorted(all_models)
        normalized = service.strip().lower()
        return sorted(self._models.get(normalized, set()))

    def get_allowed_models_dict(self) -> dict[str, list[str]]:
        return {svc: sorted(models) for svc, models in self._models.items()}

    def register_model(self, service: str, model: str) -> None:
        normalized_svc = service.strip().lower()
        self._models.setdefault(normalized_svc, set()).add(model.strip())

    def unregister_model(self, service: str, model: str) -> None:
        normalized_svc = service.strip().lower()
        if normalized_svc in self._models:
            self._models[normalized_svc].discard(model.strip())

    def toggle_model(self, service: str, model: str, enabled: bool) -> None:
        pair = (service.strip().lower(), model.strip())
        if enabled:
            self._disabled_models.discard(pair)
        else:
            self._disabled_models.add(pair)

    def is_model_enabled(self, service: str, model: str) -> bool:
        pair = (service.strip().lower(), model.strip())
        return pair not in self._disabled_models

    def is_model_allowed(self, service: str, model: str) -> bool:
        normalized_svc = service.strip().lower()
        allowed = self._models.get(normalized_svc)
        if not allowed:
            return False
        return model in allowed and self.is_model_enabled(normalized_svc, model)

    def is_alias(self, name: str) -> bool:
        return name.strip().lower() in self._aliases

    def register_alias(self, alias: str, targets: list[dict[str, str]]) -> None:
        normalized = alias.strip().lower()
        self._aliases[normalized] = [
            ModelTarget(service=t["service"], model=t["model"]) for t in targets
        ]

    def get_alias_targets(self, alias: str) -> list[ModelTarget]:
        return self._aliases.get(alias.strip().lower(), [])

    def resolve_targets(
        self, service_name: str | None, model: str
    ) -> list[ModelTarget]:
        """Resolve a requested model or alias into a ranked list of candidate ModelTargets."""
        if self.is_alias(model):
            return self.get_alias_targets(model)

        svc = service_name or getattr(config, "DEFAULT_LLM_SERVICE", "google-genai")
        return [ModelTarget(service=svc, model=model)]

    def is_valid_request(self, service_name: str | None, model: str) -> bool:
        if self.is_alias(model):
            return True
        service = service_name or "ollama"
        allowed = self._models.get(service.strip().lower())
        if allowed is not None and model not in allowed:
            return False
        return True


model_registry = DynamicModelRegistry()
