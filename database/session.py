"""
Database session management.

Supports SQLite (default) and PostgreSQL.
"""

import logging
from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from database.models import Base

logger = logging.getLogger(__name__)

# Global engine and session factory
_engine: Optional[Engine] = None
_SessionFactory: Optional[sessionmaker] = None


def get_engine(database_url: str = "sqlite:///./invoices.db") -> Engine:
    """
    Get or create database engine.

    Args:
        database_url: Database connection URL.
            SQLite: sqlite:///./invoices.db
            PostgreSQL: postgresql://user:pass@host:port/dbname

    Returns:
        SQLAlchemy Engine instance.
    """
    global _engine

    if _engine is None:
        # Configure engine based on database type
        is_sqlite = database_url.startswith("sqlite")

        connect_args = {}
        if is_sqlite:
            # SQLite specific settings
            connect_args["check_same_thread"] = False

        _engine = create_engine(
            database_url,
            connect_args=connect_args,
            echo=False,  # Set to True for SQL logging
            pool_pre_ping=True,  # Check connections before use
        )

        # Enable foreign keys for SQLite
        if is_sqlite:
            @event.listens_for(_engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        logger.info(f"Database engine created: {database_url.split('@')[-1]}")

    return _engine


def get_session_factory(engine: Optional[Engine] = None) -> sessionmaker:
    """Get or create session factory."""
    global _SessionFactory

    if _SessionFactory is None:
        if engine is None:
            engine = get_engine()
        _SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)

    return _SessionFactory


def get_session(engine: Optional[Engine] = None) -> Session:
    """
    Get a new database session.

    Args:
        engine: Optional engine to use.

    Returns:
        New Session instance.

    Note:
        Caller is responsible for closing the session.
    """
    factory = get_session_factory(engine)
    return factory()


@contextmanager
def session_scope(engine: Optional[Engine] = None) -> Generator[Session, None, None]:
    """
    Context manager for database sessions.

    Handles commit/rollback automatically.

    Usage:
        with session_scope() as session:
            session.add(model)
            # Auto-commits on exit, rolls back on exception
    """
    session = get_session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(database_url: str = "sqlite:///./invoices.db") -> Engine:
    """
    Initialize database and create all tables.

    Args:
        database_url: Database connection URL.

    Returns:
        Database engine.
    """
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    logger.info("Database tables created")
    return engine


def drop_db(database_url: str = "sqlite:///./invoices.db") -> None:
    """
    Drop all tables (use with caution!).

    Args:
        database_url: Database connection URL.
    """
    engine = get_engine(database_url)
    Base.metadata.drop_all(engine)
    logger.warning("All database tables dropped")


def reset_engine() -> None:
    """Reset global engine and session factory (for testing)."""
    global _engine, _SessionFactory
    if _engine:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
