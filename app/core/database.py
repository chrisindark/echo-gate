import logging
import os

from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///sqlite3.db")
SQLALCHEMY_DATABASE_URL_READ = os.getenv("DATABASE_URL_READ", "sqlite:///sqlite3.db")

connect_args = {}
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args=connect_args,
)
engine_read = create_engine(SQLALCHEMY_DATABASE_URL_READ, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
SessionLocalRead = sessionmaker(autocommit=False, autoflush=False, bind=engine_read)

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
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_read():
    db = SessionLocalRead()
    try:
        yield db
    finally:
        db.close()
