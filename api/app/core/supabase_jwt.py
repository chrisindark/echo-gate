"""Shared Supabase JWKS verification helpers.

Supabase issues asymmetric JWTs verified against the project's JWKS endpoint
(``SUPABASE_JWKS_URL``, or ``<SUPABASE_URL>/auth/v1/.well-known/jwks.json``).
A process-wide ``PyJWKClient`` cache avoids fetching the JWK set on every
request while remaining safe under concurrent async calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt

from app.core.config import config


class SupabaseJwtNotConfiguredError(RuntimeError):
    """Raised when Supabase JWT verification is invoked without a JWKS URL."""


@dataclass(frozen=True)
class SupabaseClaims:
    """Verified identity claims extracted from a Supabase session JWT."""

    user_id: str
    tenant_id: str
    email: str | None = None
    role: str | None = None


def resolve_supabase_jwks_url() -> str:
    """Return the effective JWKS URL from explicit config or ``SUPABASE_URL``."""
    jwks_url = (getattr(config, "SUPABASE_JWKS_URL", "") or "").strip()
    if jwks_url:
        return jwks_url
    supabase_url = (getattr(config, "SUPABASE_URL", "") or "").strip().rstrip("/")
    if supabase_url:
        return f"{supabase_url}/auth/v1/.well-known/jwks.json"
    return ""


@lru_cache(maxsize=8)
def get_jwk_client(jwks_url: str) -> jwt.PyJWKClient:
    """Return a cached ``PyJWKClient`` for ``jwks_url``."""
    return jwt.PyJWKClient(
        jwks_url,
        cache_keys=True,
        cache_jwk_set=True,
        timeout=5.0,
    )


def clear_jwk_client_cache() -> None:
    """Clear the cached ``PyJWKClient`` instances (primarily for tests)."""
    get_jwk_client.cache_clear()


def decode_supabase_jwt(token: str) -> dict[str, Any]:
    """Verify and decode a Supabase JWT using the configured JWKS endpoint."""
    jwks_url = resolve_supabase_jwks_url()
    if not jwks_url:
        raise SupabaseJwtNotConfiguredError(
            "SUPABASE_JWKS_URL (or SUPABASE_URL) is not configured on the gateway."
        )

    client = get_jwk_client(jwks_url)
    signing_key = client.get_signing_key_from_jwt(token)
    audience = (getattr(config, "SUPABASE_JWT_AUDIENCE", "") or "").strip()
    options = {"verify_aud": bool(audience)}
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=config.SUPABASE_JWT_ALGORITHMS,
        audience=audience or None,
        options=options,
    )


def extract_supabase_claims(payload: dict[str, Any]) -> SupabaseClaims | None:
    """Extract normalized tenant/user claims from a decoded Supabase JWT payload."""
    sub = payload.get("sub")
    if not sub:
        return None

    user_meta = payload.get("user_metadata") or {}
    app_meta = payload.get("app_metadata") or {}
    tenant_id = user_meta.get("tenant_id") or app_meta.get("tenant_id") or f"usr_{sub}"
    email = payload.get("email") or user_meta.get("email")
    role = payload.get("role")
    return SupabaseClaims(
        user_id=str(sub),
        tenant_id=str(tenant_id),
        email=str(email) if email else None,
        role=str(role) if role else None,
    )
