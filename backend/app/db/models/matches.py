"""Partidos y estadisticas asociadas.

Nota sobre missing data: la mayoria de columnas estadisticas son nullable.
Distintas fuentes cubren distintas variables y el pipeline (features/modelos)
debe tratar la ausencia de dato como missing, nunca inventarlo.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.database import Base


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_match_provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))
    provider_id: Mapped[str] = mapped_column(String(128))

    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"))
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"))
    matchday: Mapped[int | None] = mapped_column(Integer, nullable=True)

    kickoff_utc: Mapped[dt.datetime] = mapped_column(DateTime)

    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    referee_id: Mapped[int | None] = mapped_column(ForeignKey("referees.id"), nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="scheduled")  # scheduled|finished

    home_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_goals_ht: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_goals_ht: Mapped[int | None] = mapped_column(Integer, nullable=True)

    statistics: Mapped[MatchStatistics | None] = relationship(
        back_populates="match", uselist=False, cascade="all, delete-orphan"
    )
    odds: Mapped[list[MatchOdds]] = relationship(back_populates="match", cascade="all, delete-orphan")

    competition = relationship("Competition", foreign_keys=[competition_id])
    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])
    referee = relationship("Referee", foreign_keys=[referee_id])


class MatchStatistics(Base):
    """Estadisticas del propio partido (solo conocidas DESPUES del pitido final).

    IMPORTANTE contra leakage: estas columnas jamas deben usarse como feature
    para predecir el mismo partido. Solo sirven como input historico para
    construir features de partidos FUTUROS de esos equipos.
    """

    __tablename__ = "match_statistics"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), unique=True)

    home_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_possession: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_possession: Mapped[float | None] = mapped_column(Float, nullable=True)
    home_corners: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_corners: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_fouls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_fouls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_yellow_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_yellow_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_red_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_red_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_xg: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_xg: Mapped[float | None] = mapped_column(Float, nullable=True)

    match: Mapped[Match] = relationship(back_populates="statistics")


class MatchOdds(Base):
    """Cuotas de una casa de apuestas para un mercado/linea concretos.

    snapshot_type distingue "opening" (apertura) de "closing" (cierre), para
    no mezclar cuotas tomadas en momentos distintos del ciclo de vida del
    partido (ver docs/backtesting.md, seccion leakage de odds).
    """

    __tablename__ = "match_odds"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    bookmaker: Mapped[str] = mapped_column(String(64))
    market: Mapped[str] = mapped_column(String(64))  # p.ej. "over_under_goals"
    line: Mapped[float | None] = mapped_column(Float, nullable=True)  # p.ej. 2.5
    selection: Mapped[str] = mapped_column(String(32))  # p.ej. "over", "under", "yes", "no", "home"
    price: Mapped[float] = mapped_column(Float)
    snapshot_type: Mapped[str] = mapped_column(String(16), default="closing")
    recorded_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    match: Mapped[Match] = relationship(back_populates="odds")


class PlayerMatchStatistics(Base):
    """Placeholder para Fase 3 (jugadores). Sin poblar en el MVP."""

    __tablename__ = "player_match_statistics"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    minutes_played: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    xg: Mapped[float | None] = mapped_column(Float, nullable=True)
