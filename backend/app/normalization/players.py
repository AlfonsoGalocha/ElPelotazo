"""Normalizacion de jugadores y arbitros.

Jugadores: preparado para Fase 3 (no se pobla en el MVP, football-data.co.uk
no incluye datos a nivel de jugador).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.db.models.core import Referee


def resolve_referee_id(db: Session, raw_name: str | None) -> int | None:
    if not raw_name:
        return None
    name = raw_name.strip()
    referee = db.query(Referee).filter_by(name=name).one_or_none()
    if referee is None:
        referee = Referee(name=name)
        db.add(referee)
        db.flush()
    return referee.id
