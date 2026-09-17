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
def predictions_today(
    days: int = Query(
        7, ge=1, le=30, description="Ventana de dias hacia adelante (el futbol no juega todos los dias)"
    ),
    competition_code: str | None = Query(None),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Predicciones de partidos PROGRAMADOS entre hoy y `days` dias despues.

    No se limita al calendario estricto de hoy: la mayoria de ligas juegan en
    fin de semana o entre semana con huecos de varios dias (descansos
    internacionales, etc.), asi que "hoy" literal suele estar vacio incluso
    con datos reales. Se amplia la ventana y se deja la fecha real de cada
    partido visible en la respuesta para que el cliente la agrupe/muestre.
    """
    now = dt.datetime.utcnow()
    horizon = now + dt.timedelta(days=days)
    query = (
        db.query(Prediction)
        .join(Match, Match.id == Prediction.match_id)
        .filter(Match.status == "scheduled")
        .filter(Match.kickoff_utc >= now)
        .filter(Match.kickoff_utc <= horizon)
    )
    if competition_code:
        from backend.app.db.models.core import Competition

        competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
        if competition is None:
            return []
        query = query.filter(Match.competition_id == competition.id)

    predictions = query.order_by(Match.kickoff_utc).all()
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


@router.get("/best", response_model=list[PredictionOut])
def best_predictions(
    limit: int = Query(5, ge=1, le=50),
    market_family: str | None = Query(
        None, description="'goals', 'cards' o 'corners'; omitir para mezclar todas"
    ),
    upcoming_only: bool = Query(True),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Mejores predicciones INDEPENDIENTEMENTE del mercado (goles, tarjetas,
    corners...), pensado para un widget tipo "Top 5" que no depende de tener
    cuota de mercado (tarjetas/corners no tienen cuotas en las fuentes de
    datos usadas, ver docs/data_sources.md, asi que `/top-signals`, que
    exige edge > 0, las dejaria siempre fuera).

    Ranking transparente y distinto del de `/top-signals`:
        conviction = |model_probability - 0.5| * 2      (0 = moneda al aire, 1 = certeza del modelo)
        score = conviction * confidence * data_quality

    Sigue sin ser "la probabilidad es alta => es buena senhal": una
    probabilidad extrema con confidence/data_quality bajos puntua bajo,
    exactamente igual que en /top-signals.
    """
    query = db.query(Prediction).join(Match, Match.id == Prediction.match_id)
    if upcoming_only:
        query = query.filter(Match.status == "scheduled").filter(Match.kickoff_utc >= dt.datetime.utcnow())
    if market_family:
        prefixes = {"cards": "cards_", "corners": "corners_"}
        if market_family == "goals":
            query = query.filter(~Prediction.market.startswith("cards_")).filter(
                ~Prediction.market.startswith("corners_")
            )
        elif market_family in prefixes:
            query = query.filter(Prediction.market.startswith(prefixes[market_family]))

    candidates = query.all()

    def score(p: Prediction) -> float:
        conviction = abs(p.model_probability - 0.5) * 2.0
        return conviction * p.confidence * p.data_quality

    ranked = sorted(candidates, key=score, reverse=True)
    return [serialize_prediction(p) for p in ranked[:limit]]


@router.get("/{prediction_id}", response_model=PredictionOut)
def get_prediction(prediction_id: int, db: Session = Depends(get_db)) -> dict:
    prediction = db.get(Prediction, prediction_id)
    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediccion no encontrada")
    return serialize_prediction(prediction)
