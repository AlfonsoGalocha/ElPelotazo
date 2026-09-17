"""Configuracion global de pytest.

Fija DATABASE_URL a un SQLite temporal ANTES de importar cualquier modulo de
la app, para que el engine de backend/app/db/database.py se construya apuntando
ahi (evita tocar accidentalmente data/processed/football_edge.db real).
"""

from __future__ import annotations

import os
import tempfile

_tmp_db_fd, _tmp_db_path = tempfile.mkstemp(suffix=".db")
os.close(_tmp_db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db_path}"

import pytest  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.app.db.database import Base, engine  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    from backend.app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session():
    """Sesion aislada por test: cada test corre en una transaccion que se
    revierte al terminar, para que los tests no se contaminen entre si aunque
    compartan el mismo fichero SQLite de la sesion de pytest."""
    connection = engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(bind=connection, autoflush=False, autocommit=False, future=True)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
