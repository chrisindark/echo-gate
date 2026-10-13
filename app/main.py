import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import config
from app.core.dependencies import DependencyContainer
from app.core.logger import setup_logger
from app.core.middlewares import CorrelationIdMiddleware, UserMiddleware
from app.core.responses import BaseAPIResponse
from app.modules.analytics.analytics_controller import api_v1_router as analytics_router
from app.modules.chat.chat_controller import api_v1_router as chat_router
from app.modules.gateway_requests.gateway_requests_controller import (
    api_v1_router as gateway_requests_router,
)
from app.modules.health import health_router
from app.modules.llm_usage.llm_usage_controller import api_v1_router as llm_usage_router
from app.modules.qdrant.qdrant_controller import api_v1_router as qdrant_router
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
    await DependencyContainer.initialize()
    logger.info("EchoGate API initialized.")
    try:
        yield
    finally:
        await DependencyContainer.shutdown()
        logger.info("EchoGate API shutting down")


app = FastAPI(
    title=config.PROJECT_NAME,
    version=config.VERSION,
    description="A FastAPI-based proxy for LLMs with Semantic Caching.",
    lifespan=lifespan,
)

app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(UserMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For testing, allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Cache", "X-Correlation-ID", "X-Request-ID"],
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
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(qdrant_router)
app.include_router(llm_usage_router)
app.include_router(verifier_router)
app.include_router(gateway_requests_router)
app.include_router(analytics_router)
