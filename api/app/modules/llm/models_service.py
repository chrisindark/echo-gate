"""Model catalog for the OpenAI/Anthropic model-discovery endpoints.

The catalog is derived from ``DynamicModelRegistry`` (allowed models, aliases,
disabled flags, context windows) plus ``LlmProviderService`` configuration, so
it never becomes a second source of truth: register a model in the registry and
it shows up here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.llm.model_registry import DynamicModelRegistry, model_registry
from app.modules.llm.models_schema import (
    AnthropicModelInfo,
    AnthropicModelListResponse,
    ModelInfo,
    ModelListResponse,
    ModelTargetInfo,
)

ALIAS_OWNER = "echo-gate"

# The registry has no release dates. Report a sentinel instead of inventing one;
# `created` is required by OpenAI's schema, `created_at` by Anthropic's.
UNKNOWN_CREATED_TS = 0
UNKNOWN_CREATED_AT = "1970-01-01T00:00:00Z"


@dataclass(frozen=True)
class CatalogEntry:
    """One listable model or alias."""

    id: str
    owned_by: str
    available: bool
    enabled: bool
    providers: tuple[str, ...]
    context_window: int
    targets: tuple[ModelTargetInfo, ...] = field(default_factory=tuple)

    @property
    def visible(self) -> bool:
        """An entry is listed by default only when it can actually serve."""
        return self.enabled and self.available


class ModelCatalogService:
    """Builds the model list from the registry and provider configuration."""

    def __init__(
        self,
        provider_service: LlmProviderService,
        registry: DynamicModelRegistry | None = None,
        allowed_providers: frozenset[str] | None = None,
    ) -> None:
        self.registry = registry or model_registry
        self.availability = provider_service.service_availability()
        self.allowed_providers = (
            frozenset(p.strip().lower() for p in allowed_providers)
            if allowed_providers is not None
            else None
        )

    def entries(self, *, include_unavailable: bool = False) -> list[CatalogEntry]:
        entries = self._alias_entries() + self._model_entries()
        if include_unavailable:
            return entries
        return [entry for entry in entries if entry.visible]

    def openai_response(
        self, *, include_unavailable: bool = False
    ) -> ModelListResponse:
        entries = self.entries(include_unavailable=include_unavailable)
        return ModelListResponse(
            data=[
                ModelInfo(
                    id=entry.id,
                    created=UNKNOWN_CREATED_TS,
                    owned_by=entry.owned_by,
                    available=entry.available,
                    enabled=entry.enabled,
                    providers=list(entry.providers),
                    context_window=entry.context_window,
                    targets=list(entry.targets) or None,
                )
                for entry in entries
            ]
        )

    def anthropic_response(
        self, *, include_unavailable: bool = False
    ) -> AnthropicModelListResponse:
        entries = self.entries(include_unavailable=include_unavailable)
        data = [
            AnthropicModelInfo(
                id=entry.id,
                display_name=entry.id,
                created_at=UNKNOWN_CREATED_AT,
            )
            for entry in entries
        ]
        return AnthropicModelListResponse(
            data=data,
            first_id=data[0].id if data else None,
            last_id=data[-1].id if data else None,
        )

    def _alias_entries(self) -> list[CatalogEntry]:
        entries: list[CatalogEntry] = []
        for alias, targets in self.registry.get_aliases().items():
            filtered_targets = [
                target
                for target in targets
                if self.allowed_providers is None
                or target.service.strip().lower() in self.allowed_providers
            ]
            if not filtered_targets:
                continue
            target_info = tuple(
                ModelTargetInfo(
                    service=target.service,
                    model=target.model,
                    available=self._available(target.service),
                    enabled=self.registry.is_model_enabled(
                        target.service, target.model
                    ),
                )
                for target in filtered_targets
            )
            # The router walks the ranked targets, so the alias works if any of
            # them is both configured and enabled.
            usable = [t for t in target_info if t.available and t.enabled]
            entries.append(
                CatalogEntry(
                    id=alias,
                    owned_by=ALIAS_OWNER,
                    available=bool(usable),
                    # Aliases have no disable flag of their own.
                    enabled=True,
                    providers=tuple(t.service for t in target_info),
                    context_window=self.registry.get_context_window(alias),
                    targets=target_info,
                )
            )
        return entries

    def _model_entries(self) -> list[CatalogEntry]:
        # One provider may expose a model under two service names (gemini /
        # google-genai, claude / anthropic). Collapse by model id so SDK clients
        # never see duplicate `data[].id`, and keep the canonical service the
        # registry would pick for routing.
        hosts: dict[str, list[tuple[str, bool, bool]]] = {}
        for service, models in self.registry.get_allowed_models_dict().items():
            if (
                self.allowed_providers is not None
                and service.strip().lower() not in self.allowed_providers
            ):
                continue
            for model in models:
                hosts.setdefault(model, []).append(
                    (
                        service,
                        self._available(service),
                        self.registry.is_model_enabled(service, model),
                    )
                )

        entries: list[CatalogEntry] = []
        for model in sorted(hosts):
            services = [service for service, _, _ in hosts[model]]
            canonical_owner = self.registry.get_service_for_model(model)
            owned_by = (
                canonical_owner
                if canonical_owner
                and (
                    self.allowed_providers is None
                    or canonical_owner.strip().lower() in self.allowed_providers
                )
                else services[0]
            )
            entries.append(
                CatalogEntry(
                    id=model,
                    owned_by=owned_by,
                    available=any(available for _, available, _ in hosts[model]),
                    enabled=any(enabled for _, _, enabled in hosts[model]),
                    providers=tuple(services),
                    context_window=self.registry.get_context_window(model),
                )
            )
        return entries

    def _available(self, service: str) -> bool:
        # Unknown services (custom ALLOWED_MODELS_JSON entries) have no
        # dispatch path, so they are reported as unavailable rather than
        # silently offered to clients.
        return self.availability.get(service.strip().lower(), False)
