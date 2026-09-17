from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.db.database import get_db
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.confidence import signal_tier
from backend.app.schemas.prediction import PredictionOut

router = APIRouter(prefix="/predictions", tags=["predictions"])


def serialize_prediction(prediction: Prediction) -> dict:
    return {
        "id": prediction.id,
        "match": prediction.match,
        "market": prediction.market,
        "line": prediction.line,
        "selection": prediction.selection,
        "model_probability": prediction.model_probability,
        "market_probability": prediction.market_probability,
        "market_odds": prediction.market_odds,
        "fair_odds": prediction.fair_odds,
        "edge": prediction.edge,
        "expected_value": prediction.expected_value,
        "confidence": prediction.confidence,
        "data_quality": prediction.data_quality,
        "signal_tier": signal_tier(prediction.confidence, prediction.data_quality),
        "explanation": prediction.explanation.get("factors", []),
        "model_version_id": prediction.model_version_id,
        "created_at": prediction.created_at,
    }


@router.get("/today", response_model=list[PredictionOut])
def predictions_today(db: Session = Depends(get_db)) -> list[dict]:
    today = dt.date.today()
    predictions = (
        db.query(Prediction)
        .join(Match, Match.id == Prediction.match_id)
        .filter(Match.kickoff_utc >= dt.datetime.combine(today, dt.time.min))
        .filter(Match.kickoff_utc <= dt.datetime.combine(today, dt.time.max))
        .all()
    )
    return [serialize_prediction(p) for p in predictions]


@router.get("/top-signals", response_model=list[PredictionOut])
def top_signals(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)) -> list[dict]:
    """Ranking transparente (seccion 38): NO ordena solo por edge.

    score = edge * confidence * data_quality, solo para edge > 0.
    Esto penaliza edges grandes provenientes de modelos poco calibrados
    (confidence baja) o con pocos datos (data_quality baja), evitando la
    trampa de "edge mas alto = mejor senhal".
    """
    candidates = db.query(Prediction).filter(Prediction.edge.isnot(None), Prediction.edge > 0).all()
    scored = sorted(
        candidates,
        key=lambda p: p.edge * p.confidence * p.data_quality,
        reverse=True,
    )
    return [serialize_prediction(p) for p in scored[:limit]]


@router.get("/{prediction_id}", response_model=PredictionOut)
def get_prediction(prediction_id: int, db: Session = Depends(get_db)) -> dict:
    prediction = db.get(Prediction, prediction_id)
    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediccion no encontrada")
    return serialize_prediction(prediction)
