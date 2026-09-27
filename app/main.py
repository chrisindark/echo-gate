import os
import logging

from pathlib import Path

from fastapi import APIRouter, FastAPI
from dotenv import load_dotenv

from fastapi.middleware.cors import CORSMiddleware

from app.modules.chat.chat_controller import api_v1_router as chat_router
from app.modules.qdrant.qdrant_controller import api_v1_router as qdrant_router
# from app.modules.usage.usage_controller import router as usage_router


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

PYTHON_ENV = os.getenv("PYTHON_ENV", "local")

ENV_FILE = BASE_DIR / f".env.{PYTHON_ENV}"

if not ENV_FILE.exists():
    raise RuntimeError(f"Environment file not found: {ENV_FILE}")

load_dotenv(ENV_FILE)

logger.info(f"Loaded environment: {PYTHON_ENV}")

load_dotenv()

# from app.core.database import engine, Base
# Import all models to ensure they are registered with Base
# from app.modules.usage.usage_model import UsageLog

# Create DB schemas
# Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="LLM Gateway",
    description="A FastAPI-based proxy for LLMs with Semantic Caching.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For testing, allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Cache"],
)


api_v1_router = APIRouter(prefix="/api/v1")


@app.get("/")
def root_check():
    return {"appName": "echo-gate"}


@api_v1_router.get("/ping")
def ping_check():
    return {"pong": "ok"}


app.include_router(api_v1_router)

app.include_router(chat_router)
app.include_router(qdrant_router)
# app.include_router(usage_router)
