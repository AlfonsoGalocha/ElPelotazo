"""Fase 4: "Historico" -- partidos YA FINALIZADOS con sus predicciones y el
resultado real observado (`PredictionResult`, liquidado por
`services/evaluation_service.py::settle_finished_predictions`, nunca
inferido aqui). Filtra SIEMPRE por `Match.status == "finished"`, jamas por
fecha (un aplazamiento no deja de estar "programado" solo porque su fecha
original ya paso).
"""

from __future__ import annotations

from datetime import datetime
from math import isfinite

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.app.api.routes.predictions import serialize_prediction
from backend.app.db.database import get_db
from backend.app.db.models.core import Competition
from backend.app.db.models.features import TeamMatchFeatures
from backend.app.db.models.matches import CornerObservation, Match, MatchOddsHistory
from backend.app.db.models.modeling import Prediction, PredictionResult, PredictionSnapshot
from backend.app.db.models.publication import FixturePublication
from backend.app.prediction.eligibility import corner_verification, recorded_origin
from backend.app.prediction.opportunities import MARKET_DISAGREEMENT_THRESHOLD, market_probability_gap
from backend.app.prediction.anomaly import best_prediction_per_match
from backend.app.schemas.prediction import HistoryPredictionOut, MatchHistoryOut
from backend.app.services.evaluation_service import fit_edge_reliability_curve
from backend.app.services.publication_service import latest_model_picks, selection_outcome

router = APIRouter(prefix="/history", tags=["history"])


def _serialize_history_prediction(prediction: Prediction, is_best: bool, observed_total: int | None = None) -> dict:
    result: PredictionResult | None = prediction.result
    is_void = result is not None and result.settlement_status == "void"
    match = prediction.match
    verification = corner_verification(
        prediction.market, match.statistics if match else None, result.outcome if result else None, observed_total
    )
    base = serialize_prediction(prediction, is_best_prediction=is_best)
    return {
        **base,
        "origin_status": recorded_origin(prediction, match, datetime.utcnow()),
        "settlement_verification": verification,
        "actual_result": result.actual_result if result else None,
        # Nunca True/False para una liquidacion "void" (partido
        # cancelado): no hubo un acierto/fallo real que mostrar, el
        # frontend la trata igual que "sin resolver" para el color
        # (ambas grises), pero `settlement_status` distingue el texto.
        "is_correct": (result.outcome if result and not is_void and verification in {"confirmed", "not_applicable"} else None),
        "settled_at": result.settled_at if result else None,
        "closing_line_value": result.closing_line_value if result else None,
        "settlement_status": result.settlement_status if result else None,
    }


def _latest_corners(db: Session, match_ids: list[int]) -> dict[int, int]:
    rows = (
        db.query(CornerObservation.match_id, CornerObservation.total)
        .filter(CornerObservation.match_id.in_(match_ids))
        .order_by(CornerObservation.id).all()
    )
    return dict(rows)


def _published_selections(match, publication, corners, model_pick=None):
    stats = match.statistics
    if corners is None and stats and stats.home_corners is not None and stats.away_corners is not None:
        if min(stats.home_corners, stats.away_corners) >= 0:
            corners = stats.home_corners + stats.away_corners
    group = publication.groups.get("1X2", {}) if publication else {}
    principal = next((row for row in group.get("alternatives", []) if row["market"] == group.get("principal")), None)

    def evaluate(row, recommendation=False):
        if row is None:
            return None
        return {
            "market": row["market"],
            "probability": row["calibrated"] if recommendation and row.get("calibrated") is not None else row["probability"],
            "is_correct": selection_outcome(row["market"], match.home_goals, match.away_goals, corners),
            "odds": row.get("odds"),
            "bookmaker": row.get("bookmaker") if recommendation else None,
            "ev": row.get("estimated_ev") if recommendation else None,
            "market_probability_gap": row.get("market_probability_gap"),
            "large_market_gap": row.get("large_market_gap", False),
        }

    model_row = None
    if model_pick:
        price = model_pick.market_odds
        has_price = (
            price is not None and isfinite(price) and price > 1
            and model_pick.market_probability_source != "derived_double_chance"
        )
        gap = market_probability_gap(
            model_pick.model_probability, model_pick.market_probability, model_pick.market_probability_source,
        )
        model_row = {
            "market_probability_gap": gap,
            "large_market_gap": gap is not None and gap > MARKET_DISAGREEMENT_THRESHOLD,
            "market": model_pick.market, "probability": model_pick.model_probability,
            "odds": price if has_price else None,
        }
    return {
        "official_capture": publication is not None,
        "principal_1x2": evaluate(principal),
        "official_recommendation": evaluate(publication.recommendation, True) if publication else None,
        "evaluated_model_pick": evaluate(model_row),
    }


@router.get("", response_model=list[MatchHistoryOut])
def list_history(
    competition_code: str | None = Query(None),
    limit: int = Query(30, ge=1, le=500),
    with_predictions: bool = Query(False),
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
    if with_predictions:
        query = query.filter(or_(
            db.query(Prediction.id).filter(Prediction.match_id == Match.id).exists(),
            Match.competition.has(code="champions_league"),
        ))
    matches = query.order_by(Match.kickoff_utc.desc()).limit(limit).all()

    match_ids = [m.id for m in matches]
    if not match_ids:
        return []
    all_predictions = db.query(Prediction).filter(Prediction.match_id.in_(match_ids)).all()
    by_match: dict[int, list[Prediction]] = {}
    for p in all_predictions:
        by_match.setdefault(p.match_id, []).append(p)
    best_by_match = best_prediction_per_match(
        by_match,
        edge_reliability_curve=fit_edge_reliability_curve(db),
    )

    corner_totals = _latest_corners(db, match_ids)
    publications = {p.match_id: p for p in db.query(FixturePublication).join(
        Match, Match.id == FixturePublication.match_id,
    ).filter(FixturePublication.match_id.in_(match_ids), FixturePublication.captured_at < Match.kickoff_utc)}
    model_picks = latest_model_picks(db, match_ids)
    out: list[dict] = []
    for match in matches:
        predictions = by_match.get(match.id, [])
        if only_settled and not any(p.result is not None for p in predictions):
            continue
        best = best_by_match.get(match.id)
        if with_predictions:
            # The jornada list has one row per fixture. Full markets stay on the detail endpoint.
            predictions = [best] if best is not None else predictions[:1]
        out.append(
            {
                "match": match,
                "predictions": [
                    _serialize_history_prediction(p, best is not None and p.id == best.id, corner_totals.get(p.match_id))
                    for p in predictions
                ],
                "best_prediction_id": best.id if best else None,
                **_published_selections(match, publications.get(match.id), corner_totals.get(match.id), model_picks.get(match.id)),
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
    best = best_prediction_per_match(
        {match_id: predictions},
        edge_reliability_curve=fit_edge_reliability_curve(db),
    ).get(match_id)
    corner_totals = _latest_corners(db, [match_id])
    return {
        "match": match,
        "predictions": [
            _serialize_history_prediction(p, best is not None and p.id == best.id, corner_totals.get(p.match_id))
                    for p in predictions
        ],
        "best_prediction_id": best.id if best else None,
    }


@router.get("/{match_id}/snapshots")
def archived_predictions(match_id: int, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Partido no encontrado")
    snapshots = (
        db.query(PredictionSnapshot).filter_by(match_id=match_id)
        .order_by(PredictionSnapshot.id.desc()).limit(limit).all()
    )
    versions = {row.feature_set_version for row in snapshots if row.feature_set_version is not None}
    features = (
        db.query(TeamMatchFeatures).filter_by(match_id=match_id)
        .filter(TeamMatchFeatures.feature_set_version.in_(versions)).all()
    )
    quote_ids = {
        quote_id
        for row in snapshots
        for quote_id in (row.features_used.get("consensus_input_history_ids") or [])
    }
    odds_inputs = db.query(MatchOddsHistory).filter(
        MatchOddsHistory.id.in_(quote_ids), MatchOddsHistory.match_id == match_id
    ).all()
    return {
        "snapshots": [
            {"snapshot": row, "origin_status": recorded_origin(row, match, datetime.utcnow())}
            for row in snapshots
        ],
        "feature_snapshots": features,
        "consensus_input_quotes": odds_inputs,
    }


@router.get("/{match_id}/odds")
def archived_odds(
    match_id: int,
    market: str,
    selection: str,
    line: float | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return (
        db.query(MatchOddsHistory).filter_by(
            match_id=match_id, market=market, line=line, selection=selection
        ).order_by(MatchOddsHistory.id.desc()).limit(limit).all()
    )


@router.get("/{match_id}/corner-observations")
def corner_observations(match_id: int, db: Session = Depends(get_db)):
    return db.query(CornerObservation).filter_by(match_id=match_id).order_by(
        CornerObservation.id.desc()
    ).all()
