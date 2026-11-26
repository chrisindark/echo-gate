"""Initialize the configured Qdrant collection via the DependencyContainer."""

from app.core.config import config
from app.core.env import load_app_env

load_app_env()

from app.core.dependencies import DependencyContainer

print(
    f"Initializing Qdrant at {config.QDRANT_URL}, collection: {config.QDRANT_COLLECTION_NAME}..."
)
q = DependencyContainer.get_qdrant_client_service()
print("Reinitialized successfully.")
