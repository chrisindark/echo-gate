"""Identity provider seam.

Tenant/user identity is resolved from a server-side API key presented either as
``X-API-Key`` or as an ``Authorization: Bearer`` token (see
``TenantContextMiddleware``). This module extracts that resolution behind an
``IdentityProvider`` protocol so a cloud overlay can register
``OidcIdentityProvider`` (plus a session portal) without touching the
middleware — the middleware is the natural injection point.

OSS behavior is unchanged: ``ApiKeyIdentityProvider`` is the default and only
registered provider in this build. The Bearer alias exists so OpenAI-compatible
SDK clients, which always send ``Authorization: Bearer <key>``, work without a
client-side header override.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Header names are lowercase because Starlette's ``Headers`` lookups are
# case-insensitive while plain dicts passed to the provider are not.
API_KEY_HEADER = "x-api-key"
AUTHORIZATION_HEADER = "authorization"


def api_key_from_values(api_key: str | None, authorization: str | None) -> str | None:
    """Resolve the gateway credential from the two supported header values.

    ``X-API-Key`` wins when both are supplied, so an explicit gateway header is
    never overridden by a proxy-injected ``Authorization`` header. Only a
    ``Bearer`` scheme is accepted for the authorization header; any other scheme
    is ignored, which preserves the existing optional-auth semantics (a missing
    credential is anonymous, an invalid one is rejected by the route guard).

    Values that are not strings are ignored. This matters because FastAPI's
    ``Header(default=None)`` parameters arrive as ``Header`` instances when a
    guard is called directly in tests rather than through dependency injection.
    """
    key = api_key.strip() if isinstance(api_key, str) else ""
    if key:
        return key

    raw_authorization = authorization if isinstance(authorization, str) else ""
    scheme, separator, token = raw_authorization.strip().partition(" ")
    if separator and scheme.lower() == "bearer":
        return token.strip() or None
    return None


def api_key_from_headers(headers: Mapping[str, str]) -> str | None:
    """Resolve the gateway credential from a request header mapping."""
    return api_key_from_values(
        headers.get(API_KEY_HEADER), headers.get(AUTHORIZATION_HEADER)
    )


@dataclass(frozen=True)
class Identity:
    """Resolved tenant/user identity."""

    tenant_id: str
    user_id: str


@runtime_checkable
class IdentityProvider(Protocol):
    """Resolves an HTTP request to an optional Identity.

    Return ``None`` when this provider cannot authenticate the request; the
    chain of providers (if any) falls through, and the request is ultimately
    ``anonymous`` when no provider resolves it.
    """

    def resolve(self, headers: dict[str, str]) -> Identity | None: ...


class ApiKeyIdentityProvider:
    """OSS default: resolves an API key via the API-key store.

    Accepts ``X-API-Key`` or ``Authorization: Bearer``; ``X-API-Key`` takes
    precedence when both are present. Uses the shared ``api_key_service``
    instance (the same one the controllers and middleware always used) so
    resolution caching and test doubles both work through one service.
    """

    def resolve(self, headers: dict[str, str]) -> Identity | None:
        from app.modules.api_keys.api_keys_service import api_key_service

        key = api_key_from_headers(headers)
        if not key:
            return None
        identity = api_key_service.resolve_api_key(key)
        if identity is None:
            return None
        return Identity(tenant_id=identity.tenant_id, user_id=identity.user_id)


class SupabaseJwtIdentityProvider:
    """Resolves a Supabase JWT (Bearer token) to an authenticated tenant/user identity.

    When ``SUPABASE_AUTH_ENABLED`` and a JWKS URL (``SUPABASE_JWKS_URL`` or
    derived from ``SUPABASE_URL``) are configured, this provider verifies
    incoming Bearer JWT tokens and maps the subject (user UUID) to a personal
    tenant (``usr_<uuid>``) or an explicit ``tenant_id`` set in metadata.
    """

    def resolve(self, headers: dict[str, str]) -> Identity | None:
        from app.core.config import config
        from app.core.supabase_jwt import (
            decode_supabase_jwt,
            extract_supabase_claims,
            resolve_supabase_jwks_url,
        )

        if not config.SUPABASE_AUTH_ENABLED or not resolve_supabase_jwks_url():
            return None

        token = api_key_from_headers(headers)
        if not token or token.count(".") != 2:
            return None

        try:
            payload = decode_supabase_jwt(token)
            claims = extract_supabase_claims(payload)
            if claims is None:
                return None
            return Identity(tenant_id=claims.tenant_id, user_id=claims.user_id)
        except Exception:
            return None


class ChainedIdentityProvider:
    """Evaluates multiple identity providers in order, returning the first match."""

    def __init__(self, providers: list[IdentityProvider]) -> None:
        self._providers = providers

    def resolve(self, headers: dict[str, str]) -> Identity | None:
        for provider in self._providers:
            identity = provider.resolve(headers)
            if identity is not None:
                return identity
        return None


def default_provider() -> IdentityProvider:
    """The provider the OSS gateway uses (a cloud overlay overrides this)."""
    return ChainedIdentityProvider(
        [
            ApiKeyIdentityProvider(),
            SupabaseJwtIdentityProvider(),
        ]
    )
