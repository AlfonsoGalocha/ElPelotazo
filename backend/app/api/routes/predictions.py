from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.db.database import get_db
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction
from backend.app.config.settings import get_settings
from backend.app.prediction.confidence import signal_tier
from backend.app.prediction.market_quality import market_quality_tier
from backend.app.prediction.ranking import odds_age_minutes, rank_signals
from backend.app.schemas.prediction import CurrentRoundPredictionsOut, PredictionOut, SignalDetailOut
from backend.app.services.round_service import get_current_round, get_next_round
from backend.app.utils.logging import get_logger

router = APIRouter(prefix="/predictions", tags=["predictions"])
logger = get_logger(__name__)


def serialize_prediction(prediction: Prediction) -> dict:
    settings = get_settings()
    age_minutes = odds_age_minutes(prediction)
    is_stale = settings.max_odds_age_minutes is not None and age_minutes > settings.max_odds_age_minutes
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
        "market_quality": (
            market_quality_tier(
                prediction.bookmakers_used,
                prediction.market_odds_min,
                prediction.market_odds_max,
                prediction.market_odds_median,
                settings,
            )
            if prediction.bookmakers_used is not None
            else None
        ),
        "odds_age_minutes": round(age_minutes, 1),
        "is_stale_odds": is_stale,
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


def _resolve_scope_match_ids(db: Session, scope: str, competition_code: str | None) -> list[int]:
    """`scope="current_round"` (por defecto) o `"next_round"`: junta los
    `match_ids` de la jornada correspondiente de CADA competicion conocida
    (o solo de `competition_code` si se especifica), usando
    `services/round_service.py` -- la MISMA logica que `/current-round`,
    nunca una ventana de dias generica. Esto es lo que evita que un
    partido de la jornada siguiente aparezca en "Mejores señales" solo por
    tener mas edge (seccion 1/acceptance criteria 1-2): ese partido
    sencillamente nunca entra en la consulta SQL, no se filtra despues por
    fecha a ojo."""
    from backend.app.db.models.core import Competition

    codes = [competition_code] if competition_code else [c.code for c in db.query(Competition.code).all()]
    getter = get_current_round if scope == "current_round" else get_next_round

    match_ids: list[int] = []
    for code in codes:
        round_info = getter(db, code)
        if round_info is not None:
            match_ids.extend(round_info.match_ids)
    return match_ids


def _base_signal_query(
    db: Session,
    market_family: str | None,
    upcoming_only: bool,
    days: int | None = 4,
    date: dt.date | None = None,
    min_fair_odds: float | None = None,
    max_fair_odds: float | None = None,
    scope: str = "current_round",
    competition_code: str | None = None,
    min_edge_pp: float | None = None,
    min_bookmakers: int | None = None,
):
    """`scope` decide QUE VENTANA de partidos se considera (seccion 1 de la
    revision de arquitectura), en este orden de prioridad:

    1. `date`: un dia CONCRETO (p.ej. "solo el sabado"). Prioridad maxima
       -- puede caer fuera de la jornada actual y aun asi ser justo lo
       que se pide.
    2. `scope="current_round"` (POR DEFECTO): solo partidos de la JORNADA
       ACTUAL de cada competicion (`round_service.get_current_round`),
       nunca "los proximos N dias" -- una ventana de dias puede mezclar
       dos jornadas o dejar fuera partidos tardios de la jornada en curso.
       Este es el default pedido explicitamente: "Mejores señales" nunca
       debe mostrar un partido de la jornada siguiente por tener mas edge.
    3. `scope="next_round"`: la jornada INMEDIATAMENTE posterior.
    4. `scope="all_upcoming"`: vuelve al comportamiento anterior, una
       ventana relativa de `days` dias (o sin limite si `days=None`) --
       util para explorar mas alla de la jornada actual explicitamente.

    `competition_code`: restringe a una unica competicion; sin el, se
    consideran las 5 ligas del MVP a la vez (cada una con su propia
    jornada actual, que no tiene por que coincidir en fecha con las otras).

    `min_fair_odds`/`max_fair_odds`: filtro por CUOTA JUSTA del modelo
    (`fair_odds = 1/model_probability`, ver prediction/fair_odds.py), no
    por la cuota de mercado -- es "que probabilidad ve el modelo", no "que
    paga la casa". Independiente de MAX_SIGNAL_ODDS (que limita la cuota
    de MERCADO para evitar tiros muy largos, no para segmentar por rango).
    """
    query = db.query(Prediction).join(Match, Match.id == Prediction.match_id)
    if upcoming_only:
        query = query.filter(Match.status == "scheduled")
        if date is not None:
            day_start = dt.datetime.combine(date, dt.time.min)
            day_end = day_start + dt.timedelta(days=1)
            query = query.filter(Match.kickoff_utc >= day_start).filter(Match.kickoff_utc < day_end)
        elif scope == "all_upcoming":
            query = query.filter(Match.kickoff_utc >= dt.datetime.utcnow())
            if days is not None:
                query = query.filter(Match.kickoff_utc <= dt.datetime.utcnow() + dt.timedelta(days=days))
        else:
            match_ids = _resolve_scope_match_ids(db, scope, competition_code)
            query = query.filter(Prediction.match_id.in_(match_ids))
    if competition_code:
        from backend.app.db.models.core import Competition

        competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
        query = query.filter(Match.competition_id == (competition.id if competition else -1))
    if market_family:
        prefixes = {"cards": "cards_", "corners": "corners_"}
        if market_family == "goals":
            query = query.filter(~Prediction.market.startswith("cards_")).filter(
                ~Prediction.market.startswith("corners_")
            )
        elif market_family in prefixes:
            query = query.filter(Prediction.market.startswith(prefixes[market_family]))
    if min_fair_odds is not None:
        query = query.filter(Prediction.fair_odds >= min_fair_odds)
    if max_fair_odds is not None:
        query = query.filter(Prediction.fair_odds <= max_fair_odds)
    if min_edge_pp is not None:
        # `min_edge_pp` aqui se recibe en PUNTOS PORCENTUALES (p.ej. 5 =
        # 5pp), pensado para un slider de frontend -- `Prediction.edge` se
        # almacena como fraccion (0.05), igual que `Settings.min_edge_pp`
        # (el umbral del filtro DURO, en fraccion pese al nombre). Este
        # filtro es un AJUSTE FINO adicional por request, nunca sustituye
        # al filtro duro de `evaluate_quality_gate`.
        query = query.filter(Prediction.edge >= min_edge_pp / 100.0)
    if min_bookmakers is not None:
        query = query.filter(Prediction.bookmakers_used >= min_bookmakers)
    return query


def _filter_by_quality(included: list, quality: str | None, settings) -> list:
    """`quality`: "HIGH"|"MEDIUM"|"LOW" para quedarse SOLO con esa calidad
    de mercado, o `None`/"ALL" para no filtrar (ver prediction/market_quality.py).
    Post-filtro en Python (no en SQL): la calidad se deriva de varios
    campos ya persistidos, no es una columna propia."""
    if not quality or quality == "ALL":
        return included
    return [
        s
        for s in included
        if market_quality_tier(
            s.prediction.bookmakers_used,
            s.prediction.market_odds_min,
            s.prediction.market_odds_max,
            s.prediction.market_odds_median,
            settings,
        )
        == quality
    ]


QUALITY_QUERY = Query(
    None,
    pattern="^(HIGH|MEDIUM|LOW|ALL)$",
    description="Filtra por calidad de MERCADO (ver prediction/market_quality.py); omitir o 'ALL' = sin filtrar.",
)

SCOPE_QUERY = Query(
    "current_round",
    pattern="^(current_round|next_round|all_upcoming)$",
    description=(
        "'current_round' (por defecto): solo la jornada actual de cada competicion. "
        "'next_round': la siguiente jornada. 'all_upcoming': ventana de `days` dias, sin restriccion de jornada."
    ),
)


@router.get("/top-signals", response_model=list[PredictionOut])
def top_signals(
    limit: int = Query(20, ge=1, le=100),
    market_family: str | None = Query(None, description="'goals', 'cards' o 'corners'"),
    upcoming_only: bool = Query(True),
    scope: str = SCOPE_QUERY,
    competition_code: str | None = Query(None, description="Restringe a una competicion; sin ella, las 5 del MVP"),
    days: int = Query(4, ge=1, le=30, description="Solo con scope='all_upcoming'"),
    date: dt.date | None = Query(None, description="Filtrar a un dia concreto (YYYY-MM-DD); tiene prioridad sobre `scope`"),
    sort_by: str = Query(
        "score", pattern="^(score|edge)$", description="'score' (por defecto) o 'edge' (de mayor a menor)"
    ),
    min_fair_odds: float | None = Query(None, ge=1.0, description="Cuota justa MINIMA del modelo (1/model_probability)"),
    max_fair_odds: float | None = Query(None, ge=1.0, description="Cuota justa MAXIMA del modelo (1/model_probability)"),
    min_edge: float | None = Query(None, ge=0.0, description="Edge minimo en PUNTOS PORCENTUALES (ej. 5 = 5pp)"),
    min_bookmakers: int | None = Query(None, ge=1, description="Numero minimo de casas respaldando la cuota"),
    quality: str | None = QUALITY_QUERY,
    db: Session = Depends(get_db),
) -> list[dict]:
    """"Mejores señales": ranking transparente que SOLO considera
    predicciones con mercado real (ver prediction/ranking.py) de la
    JORNADA ACTUAL de cada competicion por defecto (`scope=current_round`,
    ver `services/round_service.py`) -- nunca "un partido de la jornada
    siguiente porque tiene mas edge" (seccion 1/acceptance criteria 1-2).
    Usa `scope=all_upcoming` para volver a una ventana de dias, o `date`
    para un dia concreto. Una prediccion sin cuota de mercado, con cuota
    invalida (incluida una cuota irrisoria tipo 1.02, ver MIN_SIGNAL_ODDS),
    sin evidencia de casas de apuestas suficiente, o con edge
    negativo/ausente NUNCA entra aqui — puede existir (ver
    `/predictions/model-only`), pero no compite en este ranking (seccion
    2/7 de la revision de arquitectura).

    `sort_by`: el filtro de calidad (que decide QUE entra) es siempre el
    mismo; `sort_by` solo cambia el ORDEN dentro de lo que ya paso el
    filtro. "score" (por defecto) es el ranking compuesto documentado en
    prediction/ranking.py (probabilidad al cuadrado x edge x confianza x
    calidad); "edge" ordena de mayor a menor edge en puntos porcentuales
    en crudo -- util para ver primero la mayor discrepancia modelo-mercado
    aunque venga de una senhal con probabilidad mas baja o menos casas
    respaldando la cuota, que el score compuesto penaliza a proposito.

    `min_fair_odds`/`max_fair_odds`: filtro por CUOTA JUSTA del modelo
    (no la de mercado), pedido explicito de usuario para acotar por
    rango (p.ej. "solo entre 1 y 2" = favoritos claros segun el modelo).

    `min_edge`/`min_bookmakers`/`quality`: filtros ADICIONALES ajustables
    por request (pensados para sliders/selectores de frontend), nunca
    sustituyen al filtro DURO de calidad (`Settings`/`evaluate_quality_gate`,
    que ya exige un minimo de casas y edge no negativo por defecto) --
    son un afinado extra sobre lo que ya paso ese filtro.
    """
    settings = get_settings()
    candidates = _base_signal_query(
        db,
        market_family,
        upcoming_only,
        days,
        date,
        min_fair_odds,
        max_fair_odds,
        scope,
        competition_code,
        min_edge,
        min_bookmakers,
    ).all()
    included, excluded = rank_signals(candidates, settings)
    if excluded:
        logger.info(
            "ranking.top_signals.excluded: total=%d reasons=%s",
            len(excluded),
            {r.reason.value: sum(1 for e in excluded if e.reason == r.reason) for r in excluded},
        )
    included = _filter_by_quality(included, quality, settings)
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
    scope: str = SCOPE_QUERY,
    competition_code: str | None = Query(None),
    days: int = Query(4, ge=1, le=30, description="Solo con scope='all_upcoming'"),
    date: dt.date | None = Query(None, description="Filtrar a un dia concreto (YYYY-MM-DD); tiene prioridad sobre `scope`"),
    min_fair_odds: float | None = Query(None, ge=1.0, description="Cuota justa MINIMA del modelo"),
    max_fair_odds: float | None = Query(None, ge=1.0, description="Cuota justa MAXIMA del modelo"),
    min_edge: float | None = Query(None, ge=0.0, description="Edge minimo en PUNTOS PORCENTUALES (ej. 5 = 5pp)"),
    min_bookmakers: int | None = Query(None, ge=1),
    quality: str | None = QUALITY_QUERY,
    db: Session = Depends(get_db),
) -> list[dict]:
    """"Las 5 mejores predicciones" (widget de portada), de la JORNADA
    ACTUAL por defecto (`scope=current_round`, ver `/predictions/top-signals`
    para la explicacion completa) — nunca "cualquier fecha futura": mezclar
    partidos de jornadas muy distintas entre si (sin fecha visible en el
    widget) parece un error de datos aunque no lo sea. Mismo criterio que
    `/top-signals` (solo mercado valido, mismo scoring), con un `limit` mas
    pequenho pensado para un resumen. Ver prediction/ranking.py para el
    filtro de calidad y la formula de puntuacion documentados.

    Predicciones sin mercado (tarjetas/corners, o goles sin odds todavia)
    NUNCA aparecen aqui: usa `/predictions/model-only` para mostrarlas por
    separado, etiquetadas explicitamente como "sin mercado".
    """
    settings = get_settings()
    candidates = _base_signal_query(
        db,
        market_family,
        upcoming_only,
        days,
        date,
        min_fair_odds,
        max_fair_odds,
        scope,
        competition_code,
        min_edge,
        min_bookmakers,
    ).all()
    included, _ = rank_signals(candidates, settings)
    included = _filter_by_quality(included, quality, settings)
    return [serialize_prediction(s.prediction) for s in included[:limit]]


@router.get("/best/debug")
def best_predictions_debug(
    market_family: str | None = Query(None),
    upcoming_only: bool = Query(True),
    scope: str = SCOPE_QUERY,
    competition_code: str | None = Query(None),
    days: int = Query(4, ge=1, le=30),
    date: dt.date | None = Query(None),
    min_fair_odds: float | None = Query(None, ge=1.0),
    max_fair_odds: float | None = Query(None, ge=1.0),
    db: Session = Depends(get_db),
) -> dict:
    """Observabilidad (seccion 13): por cada prediccion candidata, si entro
    al ranking o no y por que. Pensado para depurar "por que esta senhal no
    aparece" sin tener que adivinar leyendo logs."""
    candidates = _base_signal_query(
        db, market_family, upcoming_only, days, date, min_fair_odds, max_fair_odds, scope, competition_code
    ).all()
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
    scope: str = SCOPE_QUERY,
    competition_code: str | None = Query(None),
    days: int = Query(4, ge=1, le=30, description="Solo con scope='all_upcoming'"),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Predicciones del modelo SIN mercado disponible (tarjetas/corners
    siempre; goles cuando aun no hay cuotas). Existen y son validas, pero
    nunca compiten en `/predictions/best` ni `/predictions/top-signals`
    (seccion 2 de la revision: separar "prediccion del modelo" de "senhal
    con mercado"). El cliente debe etiquetarlas claramente como
    "Predicción del modelo — sin mercado"."""
    candidates = _base_signal_query(
        db, market_family, upcoming_only, days, None, None, None, scope, competition_code
    ).all()
    without_market = [p for p in candidates if p.market_odds is None or p.market_probability is None]
    without_market.sort(key=lambda p: abs(p.model_probability - 0.5), reverse=True)
    return [serialize_prediction(p) for p in without_market[:limit]]


def _build_explanation_summary(prediction: Prediction, serialized: dict) -> list[str]:
    """Explicacion en lenguaje llano, SOLO con numeros reales (seccion 17):
    nunca "apuesta segura"/"ganadora"/"100%". Cada linea es trazable a un
    campo concreto de la respuesta, para poder verificarla a mano."""
    lines = [f"El modelo estima {prediction.model_probability * 100:.1f}% de probabilidad para esta seleccion."]
    if prediction.market_probability is not None:
        lines.append(
            f"El mercado (consenso de {prediction.bookmakers_used or 0} casa"
            f"{'s' if (prediction.bookmakers_used or 0) != 1 else ''}) implica "
            f"{prediction.market_probability * 100:.1f}% de probabilidad."
        )
    else:
        lines.append("No hay cuota de mercado disponible para esta seleccion todavia.")
    if prediction.edge is not None:
        lines.append(f"Diferencia entre modelo y mercado (edge) = {prediction.edge * 100:+.1f} puntos porcentuales.")
    if prediction.market_odds is not None:
        lines.append(f"Cuota de mercado disponible: {prediction.market_odds:.2f}.")
    lines.append(f"Cuota justa segun el modelo (1 / probabilidad del modelo): {prediction.fair_odds:.2f}.")
    if serialized["market_quality"] is not None:
        lines.append(
            f"Calidad de mercado: {serialized['market_quality']} "
            f"({prediction.bookmakers_used or 0} casas respaldando el consenso)."
        )
    if serialized["odds_age_minutes"] is not None:
        lines.append(f"Estos datos de mercado se generaron hace {round(serialized['odds_age_minutes'])} minutos.")
    lines.append(
        f"Calidad de datos del modelo (informacion disponible de ambos equipos): "
        f"{prediction.data_quality * 100:.0f}%. Confianza del modelo: {prediction.confidence * 100:.0f}%."
    )
    return lines


@router.get("/{prediction_id}/detail", response_model=SignalDetailOut)
def get_prediction_detail(prediction_id: int, db: Session = Depends(get_db)) -> dict:
    """Detalle completo de una senhal (seccion 16/17): desglose
    bookmaker-por-bookmaker con la diferencia de cada cuota respecto al
    consenso (y si esa casa se descarto como outlier, ver
    `market/consensus.py`), mas una explicacion en lenguaje llano de por
    que aparece esta senhal, trazable a los datos reales."""
    from backend.app.market.consensus import compute_market_consensus
    from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP

    prediction = db.get(Prediction, prediction_id)
    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediccion no encontrada")

    serialized = serialize_prediction(prediction)

    raw_market = ALL_MARKET_TO_ODDS_LOOKUP.get(prediction.market, (None, None, None))[0]
    bookmaker_odds: list[dict] = []
    if raw_market is not None:
        match = db.get(Match, prediction.match_id)
        odds_rows = [
            o
            for o in match.odds
            if o.market == raw_market and o.line == prediction.line and o.selection == prediction.selection
        ]
        consensus = compute_market_consensus(match.odds, raw_market, prediction.line, prediction.selection)
        outlier_names = set(consensus.outliers_removed)
        median = consensus.median_odds
        bookmaker_odds = [
            {
                "bookmaker": row.bookmaker,
                "price": row.price,
                "snapshot_type": row.snapshot_type,
                "diff_from_consensus": (row.price - median) if median is not None else None,
                "is_outlier": row.bookmaker in outlier_names,
            }
            for row in odds_rows
        ]
        bookmaker_odds.sort(key=lambda r: r["price"])

    return {
        **serialized,
        "bookmaker_odds": bookmaker_odds,
        "explanation_summary": _build_explanation_summary(prediction, serialized),
    }


@router.get("/{prediction_id}", response_model=PredictionOut)
def get_prediction(prediction_id: int, db: Session = Depends(get_db)) -> dict:
    prediction = db.get(Prediction, prediction_id)
    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediccion no encontrada")
    return serialize_prediction(prediction)
