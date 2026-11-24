from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.db.database import get_db
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.standings import StandingsSnapshot
from backend.app.schemas.match import CompetitionOut, RoundOut
from backend.app.services.round_service import get_current_round

router = APIRouter(prefix="/competitions", tags=["competitions"])


@router.get("", response_model=list[CompetitionOut])
def list_competitions(db: Session = Depends(get_db)) -> list[Competition]:
    """Nunca devuelve una competicion deshabilitada explicitamente
    (`enabled is False`, ver db/models/core.py::Competition) -- `None`
    (competiciones ya existentes antes de anhadir ese campo) cuenta como
    habilitada, nunca se oculta por falta de dato."""
    competitions = db.query(Competition).all()
    visible = [c for c in competitions if c.enabled is not False]
    return sorted(visible, key=lambda c: (-(c.priority or 0), c.name))


@router.get("/{competition_code}/current-round", response_model=RoundOut)
def current_round(competition_code: str, db: Session = Depends(get_db)) -> dict:
    """Jornada actual de la competicion (ver services/round_service.py):
    la jornada con el `matchday` mas bajo que aun tiene partidos
    pendientes. Nunca "los proximos partidos que haya" sin mas."""
    round_info = get_current_round(db, competition_code)
    if round_info is None:
        raise HTTPException(
            status_code=404, detail="Sin partidos programados para esta competicion"
        )
    return {
        "competition_code": competition_code,
        "round": round_info.round,
        "round_name": round_info.round_name,
        "round_start": round_info.round_start,
        "round_end": round_info.round_end,
        "next_round": round_info.next_round,
        "is_fallback": round_info.is_fallback,
        "match_ids": round_info.match_ids,
    }


@router.get("/{competition_code}/standings")
def standings_as_of(
    competition_code: str,
    season: str,
    as_of: datetime | None = None,
    db: Session = Depends(get_db),
):
    query = (
        db.query(StandingsSnapshot)
        .join(Competition, Competition.id == StandingsSnapshot.competition_id)
        .join(Season, Season.id == StandingsSnapshot.season_id)
        .filter(Competition.code == competition_code, Season.label == season)
    )
    if as_of is not None:
        cutoff = as_of.astimezone(timezone.utc).replace(tzinfo=None) if as_of.tzinfo else as_of
        query = query.filter(StandingsSnapshot.as_of <= cutoff)
    captured_at = query.with_entities(StandingsSnapshot.as_of).order_by(
        StandingsSnapshot.as_of.desc()
    ).limit(1).scalar()
    if captured_at is None:
        raise HTTPException(status_code=404, detail="No existe una clasificación archivada para ese corte")
    return query.filter(StandingsSnapshot.as_of == captured_at).order_by(StandingsSnapshot.position).all()
