import hashlib
import json
import logging
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, Response, status

from app.core.config import config
from app.core.constants import EchoGateHeaders
from app.core.dependencies import get_llm_quota_service
from app.core.identity import api_key_from_headers, api_key_from_values
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
        response: Response,
        x_api_key: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        llm_quota_service: LlmQuotaService = Depends(get_llm_quota_service),
    ):
        api_key = api_key_from_values(x_api_key, authorization)
        provider = None
        model = None
        # Identity is established by TenantContextMiddleware from the API key
        # alone (X-API-Key or Authorization: Bearer): the resolved tenant/user,
        # else the anonymous tenant. Nothing the caller supplies may change it.
        # The body used to be able to override the user via `body["user"]`,
        # which both minted a fresh Redis rate-limit bucket per request and
        # split the RPM counter from the TPM counter (record_tokens keys on the
        # resolved user, not this one).
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
        bucket_id = api_key
        if not api_key:
            bucket_id = get_anonymous_bucket_id(request)

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
        quota_status = await llm_quota_service.check_quota(
            api_key=api_key,
            provider=provider,
            model=model,
            user=user,
            tenant_id=tenant_id,
            bucket_id=bucket_id,
        )

        # Advertise the remaining budget so clients can self-throttle instead of
        # discovering the limit by being rejected. Stored on request.state because
        # a StreamingResponse replaces the injected Response, so the controller has
        # to re-apply these headers on the stream it builds.
        headers = (
            quota_status.to_headers() if hasattr(quota_status, "to_headers") else {}
        )
        if headers:
            request.state.quota_status = quota_status
            for name, value in headers.items():
                response.headers[name] = value

        # Attach the resolved credential (not the raw header) to request state
        request.state.api_key = api_key
        request.state.bucket_id = bucket_id

        return True


def get_anonymous_bucket_id(request: Request) -> str:
    """Derive a stable per-caller fingerprint for anonymous requests."""
    client = getattr(request, "client", None)
    client_ip = (
        (request.headers.get("x-forwarded-for", "").split(",")[0].strip())
        or (client.host if client else None)
        or "unknown"
    )
    session_id = request.headers.get("x-session-id")
    fingerprint = f"{client_ip}:{session_id}" if session_id else client_ip
    return f"anon:{hashlib.sha256(fingerprint.encode()).hexdigest()[:16]}"


def resolve_caller_user(request: Request) -> str:
    """Resolve the caller's user identity for per-user scoping.

    Prefers the resolved user identity from auth/state. Falls back to
    a stable key fingerprint if a key was provided without an explicit user,
    and finally to the anonymous bucket ID (derived from client IP and
    optional session).
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return user_id

    user = getattr(request.state, "user", None)
    if user and user != ANONYMOUS_TENANT:
        return user

    api_key = getattr(request.state, "api_key", None) or api_key_from_headers(
        request.headers
    )
    if api_key:
        return f"key:{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"

    bucket_id = getattr(request.state, "bucket_id", None)
    if bucket_id:
        return bucket_id

    return get_anonymous_bucket_id(request)


# Initialize a singleton instance for Depends
llm_quota_guard = LlmQuotaGuard()


class ApiKeyAuthGuard:
    """Route dependency that resolves an API key to a tenant/user.

    The credential is read from ``X-API-Key`` or, for OpenAI SDK compatibility,
    from ``Authorization: Bearer <key>``. The explicit gateway header wins when
    both are present. Two modes, selected by ``allow_anonymous``:

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
        authorization: str | None = Header(default=None),
    ) -> bool:
        api_key = api_key_from_values(x_api_key, authorization)
        if not api_key:
            if self._allow_anonymous and config.ALLOW_ANONYMOUS:
                # No credential offered; the request stays anonymous. Identity
                # (tenant "anonymous", no user) comes from
                # TenantContextMiddleware, and the quota guard applies the
                # default limits to it.
                return True
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "API Key missing. Provide a valid "
                    f"{EchoGateHeaders.API_KEY} header or Authorization: Bearer token."
                ),
            )

        import asyncio

        from app.core.identity import (
            API_KEY_HEADER,
            AUTHORIZATION_HEADER,
            default_provider,
        )
        from app.modules.api_keys.api_keys_service import api_key_service

        identity = await asyncio.to_thread(api_key_service.resolve_api_key, api_key)
        if identity is None:
            # Fall back to identity provider chain (e.g. Supabase JWT)
            provider_identity = await asyncio.to_thread(
                default_provider().resolve,
                {
                    API_KEY_HEADER: x_api_key or "",
                    AUTHORIZATION_HEADER: authorization or "",
                },
            )
            if provider_identity is not None:
                request.state.api_key = api_key
                request.state.tenant_id = provider_identity.tenant_id
                request.state.user_id = provider_identity.user_id
                return True

            # Rejected in both modes: presenting a credential that does not
            # resolve is an error, not a request to fall back to anonymous.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or inactive API Key.",
            )

        request.state.api_key = api_key
        request.state.tenant_id = identity.tenant_id
        request.state.user_id = identity.user_id
        return True


api_key_auth_guard = ApiKeyAuthGuard()
api_key_auth_optional_guard = ApiKeyAuthGuard(allow_anonymous=True)


_PROVIDER_ALIASES: dict[str, str] = {
    "gemini": "google-genai",
    "google-genai": "gemini",
    "claude": "anthropic",
    "anthropic": "claude",
}


def is_anonymous_caller(request: Request) -> bool:
    """Return True when the incoming request has no resolved key or tenant."""
    if getattr(request.state, "api_key", None):
        return False
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id and tenant_id != ANONYMOUS_TENANT:
        return False
    return True


def get_anonymous_allowed_providers(request: Request) -> frozenset[str] | None:
    """Return the provider allowlist for anonymous callers, or None if unrestricted."""
    if not is_anonymous_caller(request):
        return None
    configured = getattr(config, "ANONYMOUS_ALLOWED_PROVIDERS", None)
    if configured is None:
        return None
    expanded: set[str] = set()
    for provider in configured:
        norm = provider.strip().lower()
        if not norm:
            continue
        expanded.add(norm)
        alias = _PROVIDER_ALIASES.get(norm)
        if alias:
            expanded.add(alias)
    return frozenset(expanded)


def enforce_anonymous_provider_access(http_request: Request, chat_req) -> None:
    """Restrict anonymous callers to configured free-tier providers and clamp fallbacks."""
    from app.modules.llm.model_registry import model_registry

    allowed = get_anonymous_allowed_providers(http_request)
    chat_req.allowed_providers = allowed
    if allowed is None:
        return

    configured_list = sorted(
        getattr(config, "ANONYMOUS_ALLOWED_PROVIDERS", set()) or set()
    )
    allowed_label = ", ".join(configured_list) if configured_list else "none"

    if model_registry.is_alias(chat_req.model):
        targets = model_registry.resolve_targets(chat_req.service_name, chat_req.model)
        usable = [t for t in targets if t.service.strip().lower() in allowed]
        if not usable:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": (
                        f"Model alias '{chat_req.model}' has no targets available for anonymous callers "
                        f"(allowed providers: {allowed_label}). Sign in or provide an API key."
                    ),
                    "error_code": "provider_not_allowed",
                },
            )
        return

    effective_service = (
        (
            chat_req.service_name
            or getattr(config, "DEFAULT_LLM_SERVICE", "google-genai")
        )
        .strip()
        .lower()
    )
    if effective_service not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": (
                    f"Anonymous requests are limited to: {allowed_label}. "
                    f"Sign in or provide an API key to use '{effective_service}'."
                ),
                "error_code": "provider_not_allowed",
            },
        )


@dataclass(frozen=True)
class UserSessionIdentity:
    """Identity resolved from a verified human user session (e.g. Supabase Auth JWT)."""

    user_id: str
    tenant_id: str
    email: str | None = None
    role: str | None = None


class UserAuthGuard:
    """Route dependency requiring a valid user session Bearer token (Supabase Auth).

    Guards self-service endpoints (/api/v1/user/*) where logged-in human operators
    manage their own personal tenant's API keys, profiles, and settings.
    """

    async def __call__(
        self,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> UserSessionIdentity:
        if not authorization:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required. Provide a valid Bearer token.",
            )

        scheme, _, token = authorization.strip().partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization scheme. Use 'Authorization: Bearer <token>'.",
            )

        jwt_token = token.strip()

        if not config.SUPABASE_JWT_SECRET:
            logger.error("SUPABASE_JWT_SECRET is not configured on the gateway.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Supabase JWT authentication is not configured on the gateway.",
            )

        import jwt

        try:
            options = {"verify_aud": bool(config.SUPABASE_JWT_AUDIENCE)}
            payload = jwt.decode(
                jwt_token,
                config.SUPABASE_JWT_SECRET,
                algorithms=config.SUPABASE_JWT_ALGORITHMS,
                audience=config.SUPABASE_JWT_AUDIENCE
                if config.SUPABASE_JWT_AUDIENCE
                else None,
                options=options,
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired.",
            )
        except jwt.PyJWTError as e:
            logger.warning("Supabase JWT validation failed: %s", e)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token.",
            )

        sub = payload.get("sub")
        if not sub:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is missing required subject claim.",
            )

        user_meta = payload.get("user_metadata") or {}
        app_meta = payload.get("app_metadata") or {}
        tenant_id = (
            user_meta.get("tenant_id") or app_meta.get("tenant_id") or f"usr_{sub}"
        )
        email = payload.get("email") or user_meta.get("email")
        role = payload.get("role")

        session_identity = UserSessionIdentity(
            user_id=str(sub),
            tenant_id=str(tenant_id),
            email=str(email) if email else None,
            role=str(role) if role else None,
        )

        request.state.user_id = session_identity.user_id
        request.state.tenant_id = session_identity.tenant_id
        return session_identity


user_auth_guard = UserAuthGuard()


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
                    f"Admin API Key missing. Provide the {EchoGateHeaders.ADMIN_KEY} header. "
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
