"""`_widen_varchar_column_if_needed` (db/database.py) -- bug real
reportado por el usuario (seccion 17 de docs/architecture_audit.md):
`Competition.code` era `String(16)` (pensado solo para codigos cortos de
club) y desbordo en el Postgres REAL con "uefa_nations_league" (20
caracteres), un fallo que la suite de tests local (SQLite, que NO impone
longitud de VARCHAR) nunca pudo detectar. Verificado aqui con fakes
minimos (nunca un Postgres real, no disponible en este entorno) que
comprueban la DECISION exacta: cuando ensancha, cuando no, y que nunca
toca SQLite."""

from __future__ import annotations

from sqlalchemy import Column, String, Table
from sqlalchemy.dialects import postgresql, sqlite

from backend.app.db.database import Base, _widen_varchar_column_if_needed

_REAL_DIALECTS = {"postgresql": postgresql.dialect(), "sqlite": sqlite.dialect()}


class _FakeConn:
    """`column.type.compile(dialect=...)` necesita un dialecto real de
    SQLAlchemy (no basta un objeto con `.name`) -- se usa el dialecto real
    correspondiente, nunca uno fabricado a medias, para que el SQL
    generado en la aserción sea el mismo que generaria la conexion real."""

    def __init__(self, dialect_name: str) -> None:
        self.dialect = _REAL_DIALECTS[dialect_name]
        self.executed: list[str] = []

    def execute(self, stmt) -> None:
        self.executed.append(str(stmt))


def _table_with_code_column(length: int) -> Table:
    # Tabla standalone (no registrada en Base.metadata real) solo para
    # tener un `Column` real con el tipo/longitud que el modelo declara.
    metadata = Base.metadata.__class__()
    return Table("fake_competitions", metadata, Column("code", String(length)))


def test_widens_when_the_database_column_is_shorter_than_the_model():
    table = _table_with_code_column(64)
    column = table.c.code
    conn = _FakeConn("postgresql")

    _widen_varchar_column_if_needed(conn, table, column, {"type": String(16)})

    assert len(conn.executed) == 1
    assert "ALTER TABLE fake_competitions ALTER COLUMN code TYPE" in conn.executed[0]
    assert "VARCHAR(64)" in conn.executed[0]


def test_never_narrows_when_the_database_column_is_already_longer():
    table = _table_with_code_column(16)  # el modelo "volviera" a 16 (hipotetico)
    column = table.c.code
    conn = _FakeConn("postgresql")

    _widen_varchar_column_if_needed(conn, table, column, {"type": String(64)})

    assert conn.executed == []  # nunca estrecha, aunque el modelo declare menos


def test_noop_when_the_length_already_matches():
    table = _table_with_code_column(64)
    column = table.c.code
    conn = _FakeConn("postgresql")

    _widen_varchar_column_if_needed(conn, table, column, {"type": String(64)})

    assert conn.executed == []


def test_never_touches_sqlite():
    """SQLite no impone la longitud declarada de un VARCHAR -- nunca hace
    falta (ni es el objetivo de esta funcion) tocarlo ahi, aunque la
    longitud reflejada fuera menor que la del modelo."""
    table = _table_with_code_column(64)
    column = table.c.code
    conn = _FakeConn("sqlite")

    _widen_varchar_column_if_needed(conn, table, column, {"type": String(16)})

    assert conn.executed == []


def test_noop_when_existing_column_type_has_no_length_attribute():
    """Una columna reflejada sin longitud (p.ej. TEXT en vez de VARCHAR) no
    debe hacer que esto reviente -- se trata como "no se puede comparar",
    nunca se asume nada."""
    table = _table_with_code_column(64)
    column = table.c.code
    conn = _FakeConn("postgresql")

    _widen_varchar_column_if_needed(conn, table, column, {"type": object()})

    assert conn.executed == []


def test_postgresql_and_sqlite_dialects_are_distinguishable_by_name():
    """Verificacion minima de que el nombre real de dialecto que usa la
    funcion (`conn.dialect.name`) es el mismo que usan los dialectos reales
    de SQLAlchemy -- para que el check `!= "postgresql"` no se base en una
    suposicion."""
    assert postgresql.dialect().name == "postgresql"
    assert sqlite.dialect().name == "sqlite"
