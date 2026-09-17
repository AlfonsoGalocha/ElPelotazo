"""Features precalculadas por (partido, equipo), materializadas para reproducibilidad."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class TeamMatchFeatures(Base):
    """Snapshot de features conocidas ANTES del kickoff para un equipo en un partido.

    `as_of` registra el instante de corte usado para calcular las features:
    ninguna fila de match_statistics con fecha >= as_of puede haber sido usada.
    """

    __tablename__ = "team_match_features"
    __table_args__ = (
        UniqueConstraint("match_id", "team_id", "feature_set_version", name="uq_team_match_features"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    is_home: Mapped[bool]
    feature_set_version: Mapped[str] = mapped_column(default="v1")
    as_of: Mapped[dt.datetime] = mapped_column(DateTime)
    features: Mapped[dict] = mapped_column(JSON)
