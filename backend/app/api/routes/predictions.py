from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.db.database import get_db
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.confidence import signal_tier
from backend.app.prediction.ranking import rank_signals
from backend.app.schemas.prediction import CurrentRoundPredictionsOut, PredictionOut
from backend.app.services.round_service import get_current_round
from backend.app.utils.logging import get_logger

router = APIRouter(prefix="/predictions", tags=["predictions"])
logger = get_logger(__name__)


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
        "has_market": prediction.market_odds is not None and prediction.market_probability is not None,
        "market_probability_source": prediction.market_probability_source,
        "bookmakers_count": prediction.bookmakers_count,
        "bookmakers_used": prediction.bookmakers_used,
        "market_odds_min": prediction.market_odds_min,
        "market_odds_max": prediction.market_odds_max,
        "market_odds_median": prediction.market_odds_median,
        "market_odds_average": prediction.market_odds_average,
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


@router.get("/current-round", response_model=CurrentRoundPredictionsOut)
def predictions_current_round(competition_code: str = Query(...), db: Session = Depends(get_db)) -> dict:
    """Predicciones de la JORNADA ACTUAL de una competicion (seccion 1/11 de
    la revision de arquitectura), no "los proximos partidos que haya" sin
    mas. Ver services/round_service.py para la definicion exacta de
    "jornada actual" y sus limitaciones honestas (fallback por fecha si
    los fixtures no tienen `matchday` todavia)."""
    round_info = get_current_round(db, competition_code)
    if round_info is None:
        return {"round": None, "predictions": []}

    predictions = (
        db.query(Prediction).filter(Prediction.match_id.in_(round_info.match_ids)).all()
        if round_info.match_ids
        else []
    )
    return {
        "round": {
            "competition_code": competition_code,
            "round": round_info.round,
            "round_start": round_info.round_start,
            "round_end": round_info.round_end,
            "next_round": round_info.next_round,
            "is_fallback": round_info.is_fallback,
            "match_ids": round_info.match_ids,
        },
        "predictions": [serialize_prediction(p) for p in predictions],
    }


def _base_signal_query(
    db: Session,
    market_family: str | None,
    upcoming_only: bool,
    days: int | None = 4,
    date: dt.date | None = None,
):
    """`days`: sin limite superior, "las mejores predicciones" puede mezclar
    partidos de jornadas MUY distintas entre si (una de esta semana, otra
    dentro de 3), lo que parece un error de datos aunque no lo sea (dos
    partidos del mismo equipo en jornadas distintas es normal, pero
    mostrarlos juntos sin fecha visible confunde). Por defecto se limita a
    los proximos `days` dias — "las mejores predicciones DE AHORA", no
    "de cualquier fecha futura". `None` quita el limite.

    `date`: filtro alternativo a `days`, para un dia CONCRETO (p.ej. "solo
    el sabado") en vez de una ventana relativa a ahora. Si se da, tiene
    prioridad sobre `days` -- un dia concreto puede caer fuera de la
    ventana por defecto de 4 dias y aun asi ser justo lo que se pide."""
    query = db.query(Prediction).join(Match, Match.id == Prediction.match_id)
    if upcoming_only:
        query = query.filter(Match.status == "scheduled")
        if date is not None:
            day_start = dt.datetime.combine(date, dt.time.min)
            day_end = day_start + dt.timedelta(days=1)
            query = query.filter(Match.kickoff_utc >= day_start).filter(Match.kickoff_utc < day_end)
        else:
            query = query.filter(Match.kickoff_utc >= dt.datetime.utcnow())
            if days is not None:
                query = query.filter(Match.kickoff_utc <= dt.datetime.utcnow() + dt.timedelta(days=days))
    if market_family:
        prefixes = {"cards": "cards_", "corners": "corners_"}
        if market_family == "goals":
            query = query.filter(~Prediction.market.startswith("cards_")).filter(
                ~Prediction.market.startswith("corners_")
            )
        elif market_family in prefixes:
            query = query.filter(Prediction.market.startswith(prefixes[market_family]))
    return query


@router.get("/top-signals", response_model=list[PredictionOut])
def top_signals(
    limit: int = Query(20, ge=1, le=100),
    market_family: str | None = Query(None, description="'goals', 'cards' o 'corners'"),
    upcoming_only: bool = Query(True),
    days: int = Query(4, ge=1, le=30, description="Ventana de dias hacia adelante (ignorado si se da `date`)"),
    date: dt.date | None = Query(None, description="Filtrar a un dia concreto (YYYY-MM-DD) en vez de una ventana"),
    sort_by: str = Query(
        "score", pattern="^(score|edge)$", description="'score' (por defecto) o 'edge' (de mayor a menor)"
    ),
    db: Session = Depends(get_db),
) -> list[dict]:
    """"Mejores señales": ranking transparente que SOLO considera
    predicciones con mercado real (ver prediction/ranking.py) dentro de los
    proximos `days` dias, o de un `date` concreto si se especifica. Una
    prediccion sin cuota de mercado, con cuota invalida, sin evidencia de
    casas de apuestas suficiente, o con edge negativo/ausente NUNCA entra
    aqui — puede existir (ver `/predictions/model-only`), pero no compite
    en este ranking (seccion 2/7 de la revision de arquitectura).

    `sort_by`: el filtro de calidad (que decide QUE entra) es siempre el
    mismo; `sort_by` solo cambia el ORDEN dentro de lo que ya paso el
    filtro. "score" (por defecto) es el ranking compuesto documentado en
    prediction/ranking.py (probabilidad al cuadrado x edge x confianza x
    calidad); "edge" ordena de mayor a menor edge en puntos porcentuales
    en crudo -- util para ver primero la mayor discrepancia modelo-mercado
    aunque venga de una senhal con probabilidad mas baja o menos casas
    respaldando la cuota, que el score compuesto penaliza a proposito.
    """
    candidates = _base_signal_query(db, market_family, upcoming_only, days, date).all()
    included, excluded = rank_signals(candidates)
    if excluded:
        logger.info(
            "ranking.top_signals.excluded: total=%d reasons=%s",
            len(excluded),
            {r.reason.value: sum(1 for e in excluded if e.reason == r.reason) for r in excluded},
        )
    if sort_by == "edge":
        included = sorted(included, key=lambda s: s.prediction.edge or 0.0, reverse=True)
    return [serialize_prediction(s.prediction) for s in included[:limit]]


@router.get("/best", response_model=list[PredictionOut])
def best_predictions(
    limit: int = Query(5, ge=1, le=50),
    market_family: str | None = Query(
        None, description="'goals', 'cards' o 'corners'; omitir para mezclar todas"
    ),
    upcoming_only: bool = Query(True),
    days: int = Query(4, ge=1, le=30, description="Ventana de dias hacia adelante (ignorado si se da `date`)"),
    date: dt.date | None = Query(None, description="Filtrar a un dia concreto (YYYY-MM-DD) en vez de una ventana"),
    db: Session = Depends(get_db),
) -> list[dict]:
    """"Las 5 mejores predicciones" (widget de portada), dentro de los
    proximos `days` dias, o de un `date` concreto — nunca "cualquier fecha
    futura": mezclar partidos de jornadas muy distintas entre si (sin fecha
    visible en el widget) parece un error de datos aunque no lo sea. Mismo
    criterio que `/top-signals` (solo mercado valido, mismo scoring), con
    un `limit` mas pequenho pensado para un resumen. Ver
    prediction/ranking.py para el filtro de calidad y la formula de
    puntuacion documentados.

    Predicciones sin mercado (tarjetas/corners, o goles sin odds todavia)
    NUNCA aparecen aqui: usa `/predictions/model-only` para mostrarlas por
    separado, etiquetadas explicitamente como "sin mercado".
    """
    candidates = _base_signal_query(db, market_family, upcoming_only, days, date).all()
    included, _ = rank_signals(candidates)
    return [serialize_prediction(s.prediction) for s in included[:limit]]


@router.get("/best/debug")
def best_predictions_debug(
    market_family: str | None = Query(None),
    upcoming_only: bool = Query(True),
    days: int = Query(4, ge=1, le=30),
    date: dt.date | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """Observabilidad (seccion 13): por cada prediccion candidata, si entro
    al ranking o no y por que. Pensado para depurar "por que esta senhal no
    aparece" sin tener que adivinar leyendo logs."""
    candidates = _base_signal_query(db, market_family, upcoming_only, days, date).all()
    included, excluded = rank_signals(candidates)
    return {
        "included": [
            {
                "prediction_id": s.prediction.id,
                "match_id": s.prediction.match_id,
                "market": s.prediction.market,
                "score": s.score,
                "model_probability": s.prediction.model_probability,
                "market_probability": s.prediction.market_probability,
                "market_odds": s.prediction.market_odds,
                "edge": s.prediction.edge,
                "bookmakers_used": s.prediction.bookmakers_used,
            }
            for s in included
        ],
        "excluded": [
            {
                "prediction_id": e.prediction.id,
                "match_id": e.prediction.match_id,
                "market": e.prediction.market,
                "reason": e.reason.value,
                "model_probability": e.prediction.model_probability,
                "market_probability": e.prediction.market_probability,
                "market_odds": e.prediction.market_odds,
                "edge": e.prediction.edge,
                "bookmakers_used": e.prediction.bookmakers_used,
            }
            for e in excluded
        ],
    }


@router.get("/model-only", response_model=list[PredictionOut])
def model_only_predictions(
    limit: int = Query(20, ge=1, le=100),
    market_family: str | None = Query(None),
    upcoming_only: bool = Query(True),
    days: int = Query(4, ge=1, le=30),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Predicciones del modelo SIN mercado disponible (tarjetas/corners
    siempre; goles cuando aun no hay cuotas). Existen y son validas, pero
    nunca compiten en `/predictions/best` ni `/predictions/top-signals`
    (seccion 2 de la revision: separar "prediccion del modelo" de "senhal
    con mercado"). El cliente debe etiquetarlas claramente como
    "Predicción del modelo — sin mercado"."""
    candidates = _base_signal_query(db, market_family, upcoming_only, days).all()
    without_market = [p for p in candidates if p.market_odds is None or p.market_probability is None]
    without_market.sort(key=lambda p: abs(p.model_probability - 0.5), reverse=True)
    return [serialize_prediction(p) for p in without_market[:limit]]


@router.get("/{prediction_id}", response_model=PredictionOut)
def get_prediction(prediction_id: int, db: Session = Depends(get_db)) -> dict:
    prediction = db.get(Prediction, prediction_id)
    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediccion no encontrada")
    return serialize_prediction(prediction)
