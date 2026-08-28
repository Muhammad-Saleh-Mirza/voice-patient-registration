"""Database engine, session factory, and declarative base.

The single `DATABASE_URL` env var is the only thing that differs between local
development (SQLite file) and production (managed Postgres). Nothing else in the
codebase knows or cares which engine is behind it.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()
_url = settings.normalized_database_url

# SQLite needs `check_same_thread=False` because FastAPI serves requests from a
# threadpool; Postgres does not want that argument at all.
_connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}

engine = create_engine(
    _url,
    connect_args=_connect_args,
    # Recycle connections before a managed Postgres (Neon) drops an idle one.
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """Declarative base all ORM models inherit from."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency. Yields a session and always closes it, even on error."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables if they do not exist.

    TRADE-OFF: a real service would use Alembic migrations. For a 3-hour build
    with a single, stable table, `create_all` is the correct amount of machinery.
    It is idempotent, so it is safe to call on every boot.
    """
    from app import models  # noqa: F401  (import registers the models on Base)

    Base.metadata.create_all(bind=engine)
