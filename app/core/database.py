import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TypeAlias

from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import config

logger = logging.getLogger(__name__)

SQLALCHEMY_DATABASE_URL = config.DATABASE_URL
SQLALCHEMY_DATABASE_URL_READ = config.DATABASE_URL_READ

DB_POOL_SIZE = config.DB_POOL_SIZE
DB_MAX_OVERFLOW = config.DB_MAX_OVERFLOW
DB_POOL_TIMEOUT = config.DB_POOL_TIMEOUT
DB_POOL_RECYCLE = config.DB_POOL_RECYCLE
DB_POOL_PRE_PING = config.DB_POOL_PRE_PING


def _create_db_engine(url: str):
    connect_args = {}
    engine_kwargs = {
        "pool_pre_ping": DB_POOL_PRE_PING,
    }
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if not url.endswith(":memory:") and url != "sqlite://":
            engine_kwargs.update(
                {
                    "pool_size": DB_POOL_SIZE,
                    "max_overflow": DB_MAX_OVERFLOW,
                    "pool_timeout": DB_POOL_TIMEOUT,
                    "pool_recycle": DB_POOL_RECYCLE,
                    "pool_pre_ping": True,
                }
            )
    else:
        engine_kwargs.update(
            {
                "pool_size": DB_POOL_SIZE,
                "max_overflow": DB_MAX_OVERFLOW,
                "pool_timeout": DB_POOL_TIMEOUT,
                "pool_recycle": DB_POOL_RECYCLE,
                "pool_pre_ping": True,
            }
        )
    return create_engine(url, connect_args=connect_args, **engine_kwargs)


engine = _create_db_engine(SQLALCHEMY_DATABASE_URL)

SessionFactory: TypeAlias = sessionmaker[Session] | Callable[[], Session]

SessionLocal: sessionmaker[Session] = sessionmaker(
    autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
)

Base = declarative_base()


@event.listens_for(engine, "connect")
def receive_connect(dbapi_connection, connection_record):
    """Fired when a physical database connection is established."""
    if config.DATABASE_URL.startswith("sqlite"):
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()
        except Exception as e:
            logger.warning("Failed to configure SQLite PRAGMAs: %s", e)
    logger.debug(
        "Successfully connected to database and initialized connection pool entry."
    )


@event.listens_for(engine, "checkout")
def receive_checkout(dbapi_connection, connection_record, connection_proxy):
    """Fired when a pooled connection is checked out for a request or task."""
    logger.debug("Database connection checked out from pool.")


@event.listens_for(engine, "checkin")
def receive_checkin(dbapi_connection, connection_record):
    """Fired when a connection is returned to the pool for reuse."""
    logger.debug("Database connection returned to pool.")


@event.listens_for(engine, "invalidate")
def receive_invalidate(dbapi_connection, connection_record, exception):
    """Fired when a connection encounters a fatal error and is discarded."""
    logger.error("Database connection invalidated due to error: %s", exception)


@event.listens_for(engine, "close")
def receive_close(dbapi_connection, connection_record):
    """Fired when a connection is closed during pool cleanup, recycle, or shutdown."""
    logger.debug("Database connection closed by pool.")


# Create DB schemas
def init_db():
    # This checks if the tables exist, and creates them if they don't
    Base.metadata.create_all(bind=engine)


def get_db():
    with SessionLocal() as db:
        yield db


def get_task_db_session():
    """Retrieve database session for background/Celery tasks, respecting FastAPI dependency overrides."""
    try:
        import sys

        main_mod = sys.modules.get("app.main")
        if main_mod and hasattr(main_mod, "app"):
            fastapi_app = main_mod.app
            if (
                hasattr(fastapi_app, "dependency_overrides")
                and get_db in fastapi_app.dependency_overrides
            ):
                gen = fastapi_app.dependency_overrides[get_db]()
                return next(gen)
    except Exception:
        pass
    return SessionLocal()


@contextmanager
def task_db_session(
    db_session: Session | None = None,
) -> Generator[Session]:
    """Retrieve database session for background/Celery tasks, respecting FastAPI dependency overrides and connection pool checkins without dropping in-flight HTTP connections."""
    is_external = db_session is not None
    session = db_session if is_external else get_task_db_session()
    try:
        yield session
    finally:
        if not is_external:
            try:
                import sys

                main_mod = sys.modules.get("app.main")
                fastapi_app = getattr(main_mod, "app", None) if main_mod else None
                is_overridden = bool(
                    fastapi_app
                    and hasattr(fastapi_app, "dependency_overrides")
                    and get_db in fastapi_app.dependency_overrides
                )
                if not is_overridden:
                    session.close()
            except Exception:
                session.close()
