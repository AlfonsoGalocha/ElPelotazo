"""Fase 4: "Historico" -- partidos YA FINALIZADOS con sus predicciones y el
resultado real observado (`PredictionResult`, liquidado por
`services/evaluation_service.py::settle_finished_predictions`, nunca
inferido aqui). Filtra SIEMPRE por `Match.status == "finished"`, jamas por
fecha (un aplazamiento no deja de estar "programado" solo porque su fecha
original ya paso).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.api.routes.predictions import serialize_prediction
from backend.app.db.database import get_db
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction, PredictionResult
from backend.app.prediction.anomaly import best_prediction_per_match
from backend.app.schemas.prediction import HistoryPredictionOut, MatchHistoryOut

router = APIRouter(prefix="/history", tags=["history"])


def _serialize_history_prediction(prediction: Prediction, is_best: bool) -> dict:
    result: PredictionResult | None = prediction.result
    base = serialize_prediction(prediction, is_best_prediction=is_best)
    return {
        **base,
        "actual_result": result.actual_result if result else None,
        "is_correct": result.outcome if result else None,
        "settled_at": result.settled_at if result else None,
    }


@router.get("", response_model=list[MatchHistoryOut])
def list_history(
    competition_code: str | None = Query(None),
    limit: int = Query(30, ge=1, le=200),
    only_settled: bool = Query(
        False, description="Si True, solo partidos con AL MENOS una prediccion ya liquidada"
    ),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = db.query(Match).filter(Match.status == "finished")
    if competition_code:
        competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
        if competition is None:
            return []
        query = query.filter(Match.competition_id == competition.id)
    matches = query.order_by(Match.kickoff_utc.desc()).limit(limit).all()

    match_ids = [m.id for m in matches]
    if not match_ids:
        return []
    all_predictions = db.query(Prediction).filter(Prediction.match_id.in_(match_ids)).all()
    by_match: dict[int, list[Prediction]] = {}
    for p in all_predictions:
        by_match.setdefault(p.match_id, []).append(p)
    best_by_match = best_prediction_per_match(by_match)

    out: list[dict] = []
    for match in matches:
        predictions = by_match.get(match.id, [])
        if only_settled and not any(p.result is not None for p in predictions):
            continue
        best = best_by_match.get(match.id)
        out.append(
            {
                "match": match,
                "predictions": [
                    _serialize_history_prediction(p, best is not None and p.id == best.id) for p in predictions
                ],
                "best_prediction_id": best.id if best else None,
            }
        )
    return out


@router.get("/{match_id}", response_model=MatchHistoryOut)
def get_history_detail(match_id: int, db: Session = Depends(get_db)) -> dict:
    """Detalle historico de UN partido finalizado (seccion 8 del brief):
    TODAS sus predicciones (no solo la mejor) comparadas contra la
    realidad, para poder auditar por que el modelo eligio lo que eligio."""
    match = db.get(Match, match_id)
    if match is None or match.status != "finished":
        raise HTTPException(status_code=404, detail="Partido finalizado no encontrado")

    predictions = db.query(Prediction).filter(Prediction.match_id == match_id).all()
    best = best_prediction_per_match({match_id: predictions}).get(match_id)
    return {
        "match": match,
        "predictions": [
            _serialize_history_prediction(p, best is not None and p.id == best.id) for p in predictions
        ],
        "best_prediction_id": best.id if best else None,
    }
