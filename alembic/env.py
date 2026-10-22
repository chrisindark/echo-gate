import os
import sys
from logging.config import fileConfig
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.database import SQLALCHEMY_DATABASE_URL, Base
from app.modules.admin.admin_api_keys_model import AdminApiKey
from app.modules.api_keys.api_keys_model import ApiKey
from app.modules.conversation.conversation_model import Conversation, Message
from app.modules.gateway_requests.gateway_requests_model import GatewayRequestLog
from app.modules.llm_quota.llm_quota_model import LlmQuotaRule
from app.modules.llm_usage.llm_usage_model import LlmUsageLog

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support

# Add the root directory and api directory to the python path so 'app' can be imported
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, "api"))


BASE_DIR = Path(__file__).resolve().parent

PYTHON_ENV = os.getenv("PYTHON_ENV", "local")

ENV_FILE = BASE_DIR / "../api/app" / f".env.{PYTHON_ENV}"
if not ENV_FILE.exists():
    ENV_FILE = BASE_DIR / "../app" / f".env.{PYTHON_ENV}"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)

load_dotenv()

# Migrations must run as the table owner (a superuser/owner role), because RLS
# policies do not apply to the role that owns the tables. The application itself
# connects as a separate, non-owner role (DATABASE_URL).
MIGRATION_DATABASE_URL = os.getenv("MIGRATION_DATABASE_URL", SQLALCHEMY_DATABASE_URL)

models = [
    LlmUsageLog,
    LlmQuotaRule,
    GatewayRequestLog,
    Conversation,
    Message,
    ApiKey,
    AdminApiKey,
]
config.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
