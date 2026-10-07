import logging

from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

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
                }
            )
    else:
        engine_kwargs.update(
            {
                "pool_size": DB_POOL_SIZE,
                "max_overflow": DB_MAX_OVERFLOW,
                "pool_timeout": DB_POOL_TIMEOUT,
                "pool_recycle": DB_POOL_RECYCLE,
            }
        )
    return create_engine(url, connect_args=connect_args, **engine_kwargs)


engine = _create_db_engine(SQLALCHEMY_DATABASE_URL)
engine_read = _create_db_engine(SQLALCHEMY_DATABASE_URL_READ)

SessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
)
SessionLocalRead = sessionmaker(
    autocommit=False, autoflush=False, bind=engine_read, expire_on_commit=False
)

Base = declarative_base()


@event.listens_for(engine, "connect")
def receive_connect(dbapi_connection, connection_record):
    """Fired when a physical database connection is established."""
    logger.info("Successfully connected to the master database server.")


@event.listens_for(engine, "invalidate")
def receive_invalidate(dbapi_connection, connection_record, exception):
    """Fired when a connection encounters a fatal error and is discarded."""
    logger.error(f"Master connection invalidated due to error: {exception}")


@event.listens_for(engine, "close")
def receive_close(dbapi_connection, connection_record):
    """Fired when a connection is closed."""
    logger.error("Master connection closed")


@event.listens_for(engine_read, "connect")
def receive_connect_read(dbapi_connection, connection_record):
    """Fired when a physical database connection is established."""
    logger.info("Successfully connected to the replica database server.")


@event.listens_for(engine_read, "invalidate")
def receive_invalidate_read(dbapi_connection, connection_record, exception):
    """Fired when a connection encounters a fatal error and is discarded."""
    logger.error(f"Replica connection invalidated due to error: {exception}")


# Create DB schemas
def init_db():
    # This checks if the tables exist, and creates them if they don't
    Base.metadata.create_all(bind=engine)
    Base.metadata.create_all(bind=engine_read)


def get_db():
    with SessionLocal() as db:
        yield db


def get_db_read():
    with SessionLocalRead() as db:
        yield db
