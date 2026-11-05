"""Identity provider seam.

Today, tenant/user identity is resolved only from a server-side ``X-API-Key``
(see ``TenantContextMiddleware``). This module extracts that resolution behind
an ``IdentityProvider`` protocol so a cloud overlay can register
``OidcIdentityProvider`` (plus a session portal) without touching the
middleware — the middleware is the natural injection point.

OSS behavior is unchanged: ``ApiKeyIdentityProvider`` is the default and only
registered provider in this build.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.core.config import config


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
    """OSS default: resolves ``X-API-Key`` via the API-key store.

    Uses the shared ``api_key_service`` instance (the same one the
    controllers and middleware always used) so resolution caching and
    test doubles both work through one service.
    """

    def resolve(self, headers: dict[str, str]) -> Identity | None:
        from app.modules.api_keys.api_keys_service import api_key_service

        key = (headers.get("x-api-key") or "").strip()
        if not key:
            return None
        identity = api_key_service.resolve_api_key(key)
        if identity is None:
            return None
        return Identity(tenant_id=identity.tenant_id, user_id=identity.user_id)


def default_provider() -> IdentityProvider:
    """The provider the OSS gateway uses (a cloud overlay overrides this)."""
    providers: list[IdentityProvider] = [ApiKeyIdentityProvider()]
    # The ee overlay appends OidcIdentityProvider here.
    return providers[0] if providers else ApiKeyIdentityProvider()
