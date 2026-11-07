import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.core.anonymous_telemetry import schedule_anonymous_telemetry
from app.core.config import config
from app.core.constants import EchoGateHeaders
from app.core.dependencies import DependencyContainer
from app.core.logger import setup_logger
from app.core.middlewares import (
    CorrelationIdMiddleware,
    TenantContextMiddleware,
    UserMiddleware,
)
from app.core.responses import BaseAPIResponse
from app.core.telemetry import init_telemetry
from app.modules.analytics.analytics_controller import api_v1_router as analytics_router
from app.modules.anthropic.anthropic_controller import anthropic_router
from app.modules.api_keys.api_keys_controller import api_v1_router as api_keys_router
from app.modules.chat.chat_controller import api_v1_router as chat_router
from app.modules.conversation.conversation_controller import (
    api_v1_router as conversation_router,
)
from app.modules.gateway_requests.gateway_requests_controller import (
    api_v1_router as gateway_requests_router,
)
from app.modules.health import health_router, root_health_router
from app.modules.llm.cache_admin_controller import api_v1_router as cache_admin_router
from app.modules.llm_quota.llm_quota_controller import api_v1_router as llm_quota_router
from app.modules.llm_usage.llm_usage_controller import api_v1_router as llm_usage_router
from app.modules.qdrant.qdrant_controller import api_v1_router as qdrant_router
from app.modules.shadow_cache.shadow_cache_controller import (
    api_v1_router as shadow_cache_router,
)
from app.modules.verifier.verifier_controller import api_v1_router as verifier_router

# Initialize the custom colored logger with correlation ID
setup_logger()

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

PYTHON_ENV = os.getenv("PYTHON_ENV", "local")

ENV_FILE = BASE_DIR / f".env.{PYTHON_ENV}"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)
    logger.info(f"Loaded environment file: {ENV_FILE}")
else:
    logger.info(
        f"Environment file not found at {ENV_FILE}; using system environment variables."
    )

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.load()
    init_telemetry()
    # Opt-in anonymous usage ping (config.TELEMETRY_ENABLED, default off).
    # Fail-open: scheduling never blocks startup or serving.
    telemetry_task = schedule_anonymous_telemetry()
    await DependencyContainer.initialize()
    logger.info("EchoGate API initialized.")
    try:
        yield
    finally:
        if telemetry_task is not None:
            telemetry_task.cancel()
        await DependencyContainer.shutdown()
        logger.info("EchoGate API shutting down")


app = FastAPI(
    title=config.PROJECT_NAME or "echo-gate",
    version=config.VERSION or "0.1.0",
    description="A FastAPI-based proxy for LLMs with Semantic Caching.",
    lifespan=lifespan,
    # Declared so the OpenAPI contract documents the two credential headers.
    # Enforcement itself lives in app.core.guards, not in OpenAPI security
    # requirements, because the two keys carry different semantics: X-API-Key
    # resolves a tenant/user, X-Admin-Key resolves a scoped operator.
    openapi_tags=[
        {
            "name": "LLM Quota (Admin)",
            "description": "Cross-tenant quota rule management (X-Admin-Key).",
        },
        {"name": "API Keys", "description": "Tenant API key management."},
    ],
)

API_KEY_HEADER = {"type": "apiKey", "in": "header", "name": EchoGateHeaders.API_KEY}
ADMIN_KEY_HEADER = {"type": "apiKey", "in": "header", "name": EchoGateHeaders.ADMIN_KEY}

app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(UserMiddleware)
app.add_middleware(TenantContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For testing, allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=list(EchoGateHeaders.EXPOSED_CORS_HEADERS),
)


@app.get("/", response_model=BaseAPIResponse[dict])
def root():
    return BaseAPIResponse.success_response(
        response={
            "app": config.PROJECT_NAME,
            "version": config.VERSION,
            "docs": "/docs",
            "api_prefix": "/api/v1",
        }
    )


# Register routers
app.include_router(root_health_router)
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(anthropic_router)
app.include_router(conversation_router)
app.include_router(qdrant_router)
app.include_router(llm_usage_router)
app.include_router(verifier_router)
app.include_router(gateway_requests_router)
app.include_router(analytics_router)
app.include_router(shadow_cache_router)
app.include_router(api_keys_router)
app.include_router(llm_quota_router)
app.include_router(cache_admin_router)

# Extension seam: cloud-only surfaces (e.g. /api/v1/billing/*) register here,
# and only when the ee overlay has flipped the feature registry. The OSS
# image never mounts them.
from app.core import features as _feature_registry  # noqa: E402

if _feature_registry.is_enabled("billing"):
    from app.modules.billing.billing_controller import billing_router  # noqa: E402

    app.include_router(billing_router)

# Mount interactive coverage report in local development
if PYTHON_ENV == "local":
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles

    class DevCoverageStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            htmlcov_dir = Path("htmlcov")
            htmlcov_dir.mkdir(parents=True, exist_ok=True)
            if not (htmlcov_dir / "index.html").exists():
                if Path(".coverage").exists():
                    try:
                        import coverage

                        cov = coverage.Coverage()
                        cov.load()
                        cov.html_report(directory=str(htmlcov_dir))
                    except Exception:
                        pass
            if not (htmlcov_dir / "index.html").exists():
                return HTMLResponse(
                    '<!DOCTYPE html><html><body style="font-family:sans-serif;padding:2rem;">'
                    "<h2>No Coverage Report Found</h2>"
                    "<p>Run tests with coverage first: <code>poetry run pytest --cov=api/app</code></p>"
                    "</body></html>",
                    status_code=404,
                )
            return await super().get_response(path, scope)

    htmlcov_path = Path("htmlcov")
    htmlcov_path.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/coverage",
        DevCoverageStaticFiles(directory=str(htmlcov_path), html=True, check_dir=False),
        name="coverage",
    )


def _custom_openapi() -> dict:
    """Attach the two credential schemes and per-path security to the schema.

    The routes enforce auth with plain ``Header`` dependencies (the semantics of
    the two keys differ enough that OpenAPI security requirements would be
    misleading), so the schemes are recorded here to keep the published
    contract honest about which header each surface expects.
    """
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title or "echo-gate",
        version=app.version or "0.1.0",
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
    )
    components = schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes["ApiKeyAuth"] = API_KEY_HEADER
    security_schemes["AdminApiKeyAuth"] = ADMIN_KEY_HEADER

    for path, item in schema.get("paths", {}).items():
        security_requirement = (
            [{"AdminApiKeyAuth": []}]
            if path.startswith("/api/v1/admin/")
            else [{"ApiKeyAuth": []}]
            if path.startswith(("/api/v1/analytics", "/api/v1/gateway-requests"))
            else None
        )
        if security_requirement is None:
            continue
        for method, operation in item.items():
            if method in {"get", "post", "put", "patch", "delete", "head", "options"}:
                operation["security"] = security_requirement

    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi
