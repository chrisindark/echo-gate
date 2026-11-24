"""Environment loading and synchronization utilities.

Ensures that `.env.<PYTHON_ENV>` (or `.env`) is safely loaded into `os.environ`,
re-synchronizing `config` and `database` when invoked by operational CLIs or app startup.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def load_app_env(python_env: str | None = None) -> Path | None:
    """Load the environment file for ``python_env`` into os.environ and reload config.

    Searches ``api/app/.env.<python_env>`` (defaulting to ``os.getenv("PYTHON_ENV", "local")``)
    followed by a generic ``.env`` search in ``api/app/`` and repository root.

    Returns the Path of the specific env file if one was loaded, or None.
    """
    base_dir = Path(__file__).resolve().parent.parent  # api/app
    env_name = (python_env or os.getenv("PYTHON_ENV", "local")).strip() or "local"
    env_file = base_dir / f".env.{env_name}"

    loaded_path: Path | None = None
    if env_file.exists():
        load_dotenv(env_file)
        loaded_path = env_file
        logger.info("Loaded environment file: %s", env_file)
    else:
        # Fall back to base .env in api/app/ if present
        default_env = base_dir / ".env"
        if default_env.exists():
            load_dotenv(default_env)
            loaded_path = default_env
            logger.info("Loaded environment file: %s", default_env)
        else:
            logger.info(
                "Environment file not found at %s; using system environment variables.",
                env_file,
            )

    load_dotenv()

    # Re-sync config if config was already imported
    if "app.core.config" in sys.modules:
        from app.core.config import config

        config.load()

    # Re-sync database if database was already imported
    if "app.core.database" in sys.modules:
        from app.core.database import reload_db_engine

        reload_db_engine()

    return loaded_path
