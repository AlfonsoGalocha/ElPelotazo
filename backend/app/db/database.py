"""Engine y sesiones de SQLAlchemy."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.config.settings import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        db_path = settings.database_url.split("///")[-1]
        if db_path and db_path != ":memory:":
            from pathlib import Path

            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(settings.database_url, connect_args=connect_args, future=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """Crea todas las tablas. En produccion se prefiere Alembic."""
    from backend.app.db import models  # noqa: F401  (registra los modelos)

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Dependencia FastAPI: sesion por request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Sesion para uso en scripts/CLI (fuera de FastAPI)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
