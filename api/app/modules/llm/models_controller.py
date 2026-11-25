"""Model discovery: `GET /v1/models` and its `/api/v1` alias.

The OpenAI SDK's ``client.models.list()`` and the Anthropic SDK's
``client.models.list()`` both call ``GET /v1/models`` but parse different
envelopes. The Anthropic SDK always sends ``anthropic-version``, so that header
selects the Anthropic shape; every other caller gets the OpenAI shape.

Auth mirrors chat: a supplied tenant key is validated (an unknown key is a 401)
and a keyless request is allowed. ``?include_unavailable=true`` additionally
requires a valid ``X-Admin-Key`` because disabled or unconfigured models are
operator metadata, not part of the tenant-facing catalog.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request

from app.core.dependencies import get_llm_provider_service
from app.core.guards import (
    AdminApiKeyGuard,
    api_key_auth_optional_guard,
    get_anonymous_allowed_providers,
)
from app.modules.llm.llm_provider_service import LlmProviderService
from app.modules.llm.models_schema import (
    AnthropicModelListResponse,
    ModelListResponse,
)
from app.modules.llm.models_service import ModelCatalogService

models_router = APIRouter(tags=["Models"])

_admin_guard = AdminApiKeyGuard()

# Shared description so both paths document the same behaviour.
_LIST_DESCRIPTION = (
    "List the models this gateway can serve, including routing aliases "
    "(`fast`, `cheap`, `quality`). Disabled models and models whose provider "
    "has no credentials are hidden unless `include_unavailable=true`, which "
    "requires a valid `X-Admin-Key`. Callers sending `anthropic-version` "
    "receive the Anthropic Models API envelope instead."
)


async def _list_models(
    request: Request,
    include_unavailable: bool,
    x_admin_key: str | None,
    anthropic_version: str | None,
    provider_service: LlmProviderService,
) -> ModelListResponse | AnthropicModelListResponse:
    if include_unavailable:
        await _admin_guard(request, x_admin_key=x_admin_key)

    allowed_providers = (
        None if include_unavailable else get_anonymous_allowed_providers(request)
    )
    catalog = ModelCatalogService(provider_service, allowed_providers=allowed_providers)
    if anthropic_version:
        return catalog.anthropic_response(include_unavailable=include_unavailable)
    return catalog.openai_response(include_unavailable=include_unavailable)


@models_router.get(
    "/v1/models",
    response_model=ModelListResponse | AnthropicModelListResponse,
    description=_LIST_DESCRIPTION,
    dependencies=[Depends(api_key_auth_optional_guard)],
)
@models_router.get(
    "/api/v1/models",
    response_model=ModelListResponse | AnthropicModelListResponse,
    description=_LIST_DESCRIPTION,
    dependencies=[Depends(api_key_auth_optional_guard)],
)
async def list_models(
    request: Request,
    include_unavailable: bool = Query(
        default=False,
        description=(
            "Include models that are disabled in the registry or whose "
            "provider is not configured. Requires a valid X-Admin-Key."
        ),
    ),
    x_admin_key: str | None = Header(
        default=None,
        description="Operator key; only read when include_unavailable=true.",
    ),
    anthropic_version: str | None = Header(
        default=None,
        description=(
            "Presence selects the Anthropic Models API response envelope. "
            "The Anthropic SDK sends this header on every request."
        ),
    ),
    provider_service: LlmProviderService = Depends(get_llm_provider_service),
) -> ModelListResponse | AnthropicModelListResponse:
    return await _list_models(
        request,
        include_unavailable,
        x_admin_key,
        anthropic_version,
        provider_service,
    )
