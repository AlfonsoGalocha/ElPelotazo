from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.db.database import get_db
from backend.app.db.models.core import Competition
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
        "round_start": round_info.round_start,
        "round_end": round_info.round_end,
        "next_round": round_info.next_round,
        "is_fallback": round_info.is_fallback,
        "match_ids": round_info.match_ids,
    }
