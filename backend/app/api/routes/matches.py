from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.api.routes.predictions import serialize_prediction
from backend.app.db.database import get_db
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
    db: Session = Depends(get_db),
) -> list[Match]:
    query = db.query(Match)
    if competition_code:
        from backend.app.db.models.core import Competition

        competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
        if competition is None:
            return []
        query = query.filter(Match.competition_id == competition.id)
    if date_from:
        query = query.filter(Match.kickoff_utc >= dt.datetime.combine(date_from, dt.time.min))
    if date_to:
        query = query.filter(Match.kickoff_utc <= dt.datetime.combine(date_to, dt.time.max))
    return query.order_by(Match.kickoff_utc).all()


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
