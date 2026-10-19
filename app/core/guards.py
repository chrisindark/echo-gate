import hashlib
import json
import logging

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.dependencies import get_llm_quota_service
from app.core.rls import ANONYMOUS_TENANT
from app.modules.admin.admin_api_keys_service import AdminKeyIdentity
from app.modules.llm_quota.llm_quota_service import LlmQuotaService

logger = logging.getLogger(__name__)


class LlmQuotaGuard:
    """
    Can be used as a route dependency.
    """

    async def __call__(
        self,
        request: Request,
        x_api_key: str | None = Header(default=None),
        llm_quota_service: LlmQuotaService = Depends(get_llm_quota_service),
    ):
        provider = None
        model = None
        # Identity is established by TenantContextMiddleware from X-API-Key
        # alone: the resolved tenant/user, else the anonymous tenant. Nothing
        # the caller supplies may change it. The body used to be able to
        # override the user via `body["user"]`, which both minted a fresh Redis
        # rate-limit bucket per request and split the RPM counter from the TPM
        # counter (record_tokens keys on the resolved user, not this one).
        user = getattr(request.state, "user_id", None) or getattr(
            request.state, "user", None
        )
        tenant_id = getattr(request.state, "tenant_id", None) or ANONYMOUS_TENANT

        # Rate-limit bucket identity. Keyed callers bucket on the API key;
        # anonymous callers bucket on a stable per-caller fingerprint so one
        # keyless client cannot exhaust the shared anonymous bucket for every
        # other anonymous caller. The fingerprint is hashed before it touches
        # Redis, and combines the client address (preferring the first
        # X-Forwarded-For hop, which is what a fronting proxy reports) with an
        # optional X-Session-ID so callers behind shared NAT can still be
        # isolated from each other. A caller can rotate these values to get a
        # fresh bucket, so this bounds honest anonymous load only; it is not
        # a security boundary.
        bucket_id = x_api_key
        if not x_api_key:
            client = getattr(request, "client", None)
            client_ip = (
                (request.headers.get("x-forwarded-for", "").split(",")[0].strip())
                or (client.host if client else None)
                or "unknown"
            )
            session_id = request.headers.get("x-session-id")
            fingerprint = f"{client_ip}:{session_id}" if session_id else client_ip
            bucket_id = f"anon:{hashlib.sha256(fingerprint.encode()).hexdigest()[:16]}"

        try:
            # Safely read body. FastAPI caches request.body() so it's safe to read multiple times
            body_bytes = await request.body()
            if body_bytes:
                body = json.loads(body_bytes)
                provider = body.get("service_name")
                model = body.get("model")
        except Exception:
            logger.exception("Error reading request body")

        # Check quota
        await llm_quota_service.check_quota(
            api_key=x_api_key,
            provider=provider,
            model=model,
            user=user,
            tenant_id=tenant_id,
            bucket_id=bucket_id,
        )

        # Attach API key to request state
        request.state.api_key = x_api_key

        return True


# Initialize a singleton instance for Depends
llm_quota_guard = LlmQuotaGuard()


class ApiKeyAuthGuard:
    """Route dependency that resolves ``X-API-Key`` to a tenant/user.

    Two modes, selected by ``allow_anonymous``:

    * **Strict** (``api_key_auth_guard``, the default) — a valid key is
      required. A missing, unknown, or inactive key is a ``401``. Used by the
      tenant-scoped read surfaces (``/analytics``, ``/gateway-requests``), where
      the response is another tenant's data.

    * **Optional** (``api_key_auth_optional_guard``) — a key is authenticated
      *when supplied*: an unknown or inactive key is still a ``401``, but a
      request with no key at all proceeds as the ``anonymous`` tenant. Used by
      chat, where anonymous access is an intentional feature.

    A valid key is the only accepted credential in either mode. This guard
    previously also let a request through on the strength of an ``X-Tenant-ID``
    header alone, which meant anyone who could reach the gateway could read
    another tenant's traces and analytics by guessing a tenant name. That header
    no longer authenticates anything, and no longer selects the tenant context
    either: ``TenantContextMiddleware`` derives identity from the key alone.
    """

    def __init__(self, *, allow_anonymous: bool = False) -> None:
        self._allow_anonymous = allow_anonymous

    async def __call__(
        self,
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> bool:
        if not x_api_key:
            if self._allow_anonymous:
                # No credential offered; the request stays anonymous. Identity
                # (tenant "anonymous", no user) comes from
                # TenantContextMiddleware, and the quota guard applies the
                # default limits to it.
                return True
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API Key missing. Provide a valid X-API-Key header.",
            )

        import asyncio

        from app.modules.api_keys.api_keys_service import api_key_service

        identity = await asyncio.to_thread(api_key_service.resolve_api_key, x_api_key)
        if identity is None:
            # Rejected in both modes: presenting a credential that does not
            # resolve is an error, not a request to fall back to anonymous.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or inactive API Key.",
            )

        request.state.api_key = x_api_key
        request.state.tenant_id = identity.tenant_id
        request.state.user_id = identity.user_id
        return True


api_key_auth_guard = ApiKeyAuthGuard()
api_key_auth_optional_guard = ApiKeyAuthGuard(allow_anonymous=True)


class AdminApiKeyGuard:
    """Route dependency requiring a valid, in-scope ``X-Admin-Key``.

    Guards the cross-tenant admin surface (``/api/v1/admin/*``). Deliberately
    separate from :class:`ApiKeyAuthGuard`:

    * A different header (``X-Admin-Key``), so admin keys never pass through
      ``TenantContextMiddleware``'s resolution path and can never be turned into
      a tenant identity.
    * Admin keys live in their own table and carry scopes, not a tenant/user
      pair.

    **Fails closed.** A missing, unknown, inactive, or expired key is rejected
    even when no admin keys exist at all, so the surface is locked until an
    operator mints one with ``app/scripts/create_admin_key.py``. There is
    deliberately no HTTP path that can create an admin key.
    """

    async def __call__(
        self,
        request: Request,
        x_admin_key: str | None = Header(default=None),
        required_scope: str | None = None,
    ) -> AdminKeyIdentity:
        # Imported lazily to keep this module import-light and to avoid a
        # circular import (admin service -> api_keys service -> ...).
        import asyncio

        from app.modules.admin.admin_api_keys_service import admin_api_key_service

        if not x_admin_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "Admin API Key missing. Provide the X-Admin-Key header. "
                    "Keys are created with "
                    "`python -m app.scripts.create_admin_key create`."
                ),
            )

        identity = await asyncio.to_thread(
            admin_api_key_service.resolve_admin_key, x_admin_key
        )
        if identity is None:
            # Same response for unknown/inactive/expired so the endpoint cannot
            # be used to probe which keys exist.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid, inactive, or expired Admin API Key.",
            )

        if required_scope and not identity.has_scope(required_scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This Admin API Key lacks the '{required_scope}' scope. "
                    f"It carries: {', '.join(sorted(identity.scopes)) or 'none'}."
                ),
            )

        request.state.admin_key_id = identity.key_id
        request.state.admin_key_name = identity.name
        return identity


def require_admin_scope(required_scope: str) -> AdminApiKeyGuard:
    """Build a guard dependency that also enforces ``required_scope``.

    Usage::

        @router.get("/", admin: AdminKeyIdentity = Depends(require_admin_scope("quota:read")))

    ``quota:write`` satisfies ``quota:read``; see
    :meth:`AdminKeyIdentity.has_scope`.

    Returns an **instance**, not the class. Passing a class to ``Depends`` tells
    FastAPI it is a dependency *factory*: FastAPI instantiates it and uses the
    instance as the resolved value without ever calling ``__call__``, so the
    guard would silently never run. ``api_key_auth_guard`` works because it is
    already an instance.
    """

    class _ScopedAdminApiKeyGuard(AdminApiKeyGuard):
        async def __call__(  # type: ignore[override]
            self,
            request: Request,
            x_admin_key: str | None = Header(default=None),
        ) -> AdminKeyIdentity:
            return await super().__call__(
                request,
                x_admin_key=x_admin_key,
                required_scope=required_scope,
            )

    return _ScopedAdminApiKeyGuard()
