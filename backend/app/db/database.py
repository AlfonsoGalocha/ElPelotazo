"""Engine y sesiones de SQLAlchemy."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
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
    _sync_missing_columns()


def _sync_missing_columns() -> None:
    """Anade columnas nuevas a tablas YA EXISTENTES via `ALTER TABLE ADD
    COLUMN`, ademas de `create_all()` (que solo crea tablas nuevas, nunca
    modifica una tabla existente).

    Sin esto, cualquier persona con una base de datos SQLite local previa a
    este cambio (creada antes de anadir columnas como
    `predictions.bookmakers_count` o `matches.matchday`) veria
    `OperationalError: no such column` en cuanto el codigo intentara leer o
    escribir esa columna, y la unica solucion seria borrar la base de datos
    entera. Alembic (migraciones "de verdad", con historial y downgrade) es
    la herramienta correcta para produccion, pero este proyecto no lo tiene
    aun cableado; esto es un parche minimo y seguro (solo ANADE columnas
    nullable, nunca borra ni renombra nada) mientras tanto.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                column_type = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}"))


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
