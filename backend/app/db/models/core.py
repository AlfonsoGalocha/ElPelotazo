"""Entidades base: competiciones, temporadas, equipos, arbitros, jugadores."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.database import Base


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)  # p.ej. "E0", "SP1"
    name: Mapped[str] = mapped_column(String(120))  # p.ej. "Premier League"
    country: Mapped[str] = mapped_column(String(64))

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
