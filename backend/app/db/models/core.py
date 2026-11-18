"""Entidades base: competiciones, temporadas, equipos, arbitros, jugadores."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.database import Base


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 64, no 16 (seccion 17 de architecture_audit.md -- bug real reportado
    # por el usuario): 16 bastaba para codigos cortos de club
    # ("laliga", "premier_league") pero desbordaba con codigos de
    # selecciones mas largos ("uefa_nations_league", 20 caracteres) en el
    # Postgres real del usuario -- nunca detectado por los tests locales
    # porque SQLite no impone la longitud declarada de VARCHAR. Ver
    # `_widen_varchar_column_if_needed` en db/database.py: ensancha esta
    # columna sola en una BD Postgres ya existente (nunca la estrecha).
    code: Mapped[str] = mapped_column(String(64), unique=True)  # p.ej. "E0", "SP1"
    name: Mapped[str] = mapped_column(String(120))  # p.ej. "Premier League"
    country: Mapped[str] = mapped_column(String(64))
    # CompetitionConfig centralizado (seccion 14/15 de la revision de
    # arquitectura, antes sin campos propios -- cada decision de
    # priorizacion vivia dispersa en codigo). "CORE" = las ligas del MVP
    # ya seguidas de verdad; "EXPERIMENTAL" = anhadidas para probar
    # cobertura de datos antes de confiar en sus predicciones igual que
    # las CORE. Puramente informativo por ahora (no cambia el pipeline de
    # entrenamiento/prediccion, que trata todas las competiciones igual) --
    # lo unico que consume esto hoy es el listado publico de /competitions
    # (api/routes/competitions.py), que oculta las deshabilitadas.
    #
    # NULLABLE a proposito, aunque conceptualmente tengan un valor por
    # defecto: `_sync_missing_columns` (db/database.py) anhade columnas via
    # `ALTER TABLE ADD COLUMN` SIN DEFAULT a nivel de BD, asi que cualquier
    # fila YA EXISTENTE quedaria con NULL, no con el default de Python (que
    # solo aplica a filas nuevas insertadas por el ORM). Si estas columnas
    # fueran NOT NULL, un filtro `WHERE enabled = true` excluiria en
    # silencio TODAS las competiciones ya existentes tras esta migracion --
    # exactamente lo que este patron existe para evitar. El codigo que lee
    # estos campos trata `None` como el default (`tier or "CORE"`,
    # `enabled is not False`, `priority or 0`), nunca los deja fuera de un
    # filtro por ser NULL.
    tier: Mapped[str | None] = mapped_column(String(16), nullable=True, default="CORE")
    enabled: Mapped[bool | None] = mapped_column(nullable=True, default=True)
    priority: Mapped[int | None] = mapped_column(nullable=True, default=0)  # mayor = antes

    seasons: Mapped[list[Season]] = relationship(back_populates="competition")


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("competition_id", "label", name="uq_season_competition"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"))
    label: Mapped[str] = mapped_column(String(16))  # p.ej. "2023/24"
    start_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    competition: Mapped[Competition] = relationship(back_populates="seasons")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(120), unique=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Escudo REAL del equipo (nunca inventado/generico), capturado de
    # `payload["teams"]["home"/"away"]["logo"]` al ingerir fixtures de
    # API-Football (ver fixtures_provider.py y data_service.py::_upsert_match)
    # -- API-Football ya lo trae gratis en la misma llamada que trae el
    # partido, sin peticion ni coste adicional. `None` para equipos que
    # nunca han aparecido en un fixture de API-Football todavia (p.ej. solo
    # historico antiguo de football-data.co.uk) -- el frontend cae a un
    # placeholder elegante en ese caso, nunca a una imagen inventada.
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class TeamNameMapping(Base):
    """Resuelve nombres de una fuente concreta a un team_id canonico.

    Ejemplo: ("football_data_co_uk", "Man United") -> team_id de "Manchester United".
    Evita joins por string entre fuentes con distintas convenciones de nombres.
    """

    __tablename__ = "team_name_mappings"
    __table_args__ = (UniqueConstraint("source", "source_name", name="uq_team_mapping_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    source_name: Mapped[str] = mapped_column(String(120))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))


class Referee(Base):
    __tablename__ = "referees"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)


class DataSource(Base):
    """Registro de que fuentes alimentaron cada dato, para trazabilidad."""

    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)


class RankingConfig(Base):
    """Preferencia de usuario editable EN RUNTIME (pedido real del usuario,
    "cambio de filosofia del ranking": banda de cuotas configurable en
    Configuracion) -- la PRIMERA de este tipo en el proyecto; hasta ahora
    toda configuracion vivia en `Settings` (variables de entorno, fijas
    hasta reiniciar el proceso, ver api/routes/health.py::data_sources_status).

    Fila UNICA (siempre `id=1`, ver services/ranking_config_service.py):
    no hace falta mas de una, y asi nunca hay ambiguedad sobre "cual es la
    vigente". `None` en cualquier campo significa "usar el valor por
    defecto de `Settings`" (1.35/1.90), nunca un 0 o una cadena vacia --
    mismo principio de "nunca inventar un valor" que el resto del proyecto.
    """

    __tablename__ = "ranking_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    min_opportunity_odds: Mapped[float | None] = mapped_column(nullable=True)
    max_opportunity_odds: Mapped[float | None] = mapped_column(nullable=True)
