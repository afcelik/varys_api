"""Database connection and session management."""

from collections.abc import Generator
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    """Configure SQLite connection with WAL mode and foreign keys enabled."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


def create_engine_with_config() -> Engine:
    """Create SQLAlchemy engine with SQLite-specific configuration."""
    engine = create_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        pool_pre_ping=True,
    )

    # Apply SQLite pragmas on connection
    if settings.DATABASE_URL.startswith("sqlite"):
        event.listen(engine, "connect", _configure_sqlite_connection)

    return engine


engine = create_engine_with_config()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency to get a database session.

    Yields:
        Session: SQLAlchemy database session.

    Usage:
        @app.get("/items")
        def get_items(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Initialize the database (create tables)."""
    # Import models to register them
    from app.models import task  # noqa: F401
    from app.models.base import Base

    Base.metadata.create_all(bind=engine)