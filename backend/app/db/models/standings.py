"""Clasificacion historica por fecha (Bloque 3.4/3.5 de la mision: gap
real de esquema confirmado por auditoria -- hasta ahora la clasificacion
de una liga solo podia verse "en vivo" calculandola al vuelo desde
`Match`, nunca reconstruir "como estaba la tabla el 15 de marzo"). Mismo
principio APPEND-ONLY que `MatchOddsHistory`/`OddsIngestionRun`/
`PredictionSnapshot`: cada vez que se ingieren fixtures nuevos de una
competicion (ver services/standings_service.py, llamado desde
`cli.py::update_fixtures`) se inserta una FILA NUEVA por equipo con el
`as_of` de ese momento, nunca se actualiza una fila existente.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class StandingsSnapshot(Base):
    __tablename__ = "standings_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"))
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    # Instante real en que se calculo esta fila (nunca la fecha del
    # ULTIMO partido incluido -- ver `matchday_ceiling` para eso) --
    # mismo significado que `TeamMatchFeatures.as_of`.
    as_of: Mapped[dt.datetime] = mapped_column(DateTime)
    # Jornada mas alta YA FINALIZADA incluida en este calculo. `None`
    # cuando algun partido finalizado de la competicion no tiene
    # `matchday` conocido (fuentes antiguas) -- nunca un numero inventado.
    matchday_ceiling: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[int] = mapped_column(Integer)
    played: Mapped[int] = mapped_column(Integer)
    won: Mapped[int] = mapped_column(Integer)
    drawn: Mapped[int] = mapped_column(Integer)
    lost: Mapped[int] = mapped_column(Integer)
    goals_for: Mapped[int] = mapped_column(Integer)
    goals_against: Mapped[int] = mapped_column(Integer)
    goal_difference: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
