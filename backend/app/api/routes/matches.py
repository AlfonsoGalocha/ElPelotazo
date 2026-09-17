from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, aliased

from backend.app.api.routes.predictions import serialize_prediction
from backend.app.db.database import get_db
from backend.app.db.models.core import Competition, Team
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction
from backend.app.schemas.match import MatchOut
from backend.app.schemas.prediction import PredictionOut

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=list[MatchOut])
def list_matches(
    competition_code: str | None = None,
    date_from: dt.date | None = Query(None),
    date_to: dt.date | None = Query(None),
    search: str | None = Query(None, description="Busca por nombre de equipo (local o visitante)"),
    status: str | None = Query(None, description="'scheduled' o 'finished'"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[Match]:
    query = db.query(Match)
    if competition_code:
        competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
        if competition is None:
            return []
        query = query.filter(Match.competition_id == competition.id)
    if date_from:
        query = query.filter(Match.kickoff_utc >= dt.datetime.combine(date_from, dt.time.min))
    if date_to:
        query = query.filter(Match.kickoff_utc <= dt.datetime.combine(date_to, dt.time.max))
    if status:
        query = query.filter(Match.status == status)
    if search:
        pattern = f"%{search.strip()}%"
        HomeTeam, AwayTeam = aliased(Team), aliased(Team)
        query = (
            query.join(HomeTeam, Match.home_team_id == HomeTeam.id)
            .join(AwayTeam, Match.away_team_id == AwayTeam.id)
            .filter(or_(HomeTeam.canonical_name.ilike(pattern), AwayTeam.canonical_name.ilike(pattern)))
        )
        # Sin filtro de fecha explicito, lo mas util al buscar es lo mas
        # PROXIMO a hoy (pasado o futuro), no el partido mas antiguo del
        # historico (que es lo que daria un simple ORDER BY kickoff_utc ASC).
        # Se ordena en Python (no en SQL) para no depender de funciones de
        # fecha especificas de un motor de BD concreto (SQLite vs Postgres).
        if not date_from and not date_to:
            now = dt.datetime.utcnow()
            candidates = query.all()
            candidates.sort(key=lambda m: abs((m.kickoff_utc - now).total_seconds()))
            return candidates[:limit]
    return query.order_by(Match.kickoff_utc).limit(limit).all()


@router.get("/{match_id}", response_model=MatchOut)
def get_match(match_id: int, db: Session = Depends(get_db)) -> Match:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Partido no encontrado")
    return match


@router.get("/{match_id}/predictions", response_model=list[PredictionOut])
def get_match_predictions(match_id: int, db: Session = Depends(get_db)) -> list[dict]:
    predictions = db.query(Prediction).filter(Prediction.match_id == match_id).all()
    return [serialize_prediction(p) for p in predictions]
