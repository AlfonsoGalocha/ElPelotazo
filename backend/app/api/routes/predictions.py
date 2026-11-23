from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from backend.app.config.settings import Settings, get_settings
from backend.app.db.database import get_db
from backend.app.db.models.matches import Match, MatchOdds, MatchOddsHistory
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.anomaly import (
    best_prediction_per_match,
    compute_anomaly_flags,
    is_strong_signal,
)
from backend.app.prediction.confidence import signal_tier
from backend.app.prediction.market_quality import market_quality_tier
from backend.app.prediction.opportunities import assess_opportunity, select_ranked, valid_probability
from backend.app.prediction.ranking import (
    calibrated_probability_or_fallback,
    conservative_probability,
    ev_with_probability,
    odds_age_minutes,
    rank_signals_v3,
)
from backend.app.schemas.prediction import (
    BestOfDayOut,
    CurrentRoundPredictionsOut,
    PredictionOut,
    AvailablePredictionOut,
    SignalDetailOut,
    SummaryOut,
)
from backend.app.services.evaluation_service import (
    TrackRecordEstimate,
    fit_edge_reliability_curve,
    market_track_records,
    market_track_records_with_provenance,
)
from backend.app.services.ranking_config_service import get_opportunity_odds_band
from backend.app.services.round_service import get_current_round, get_next_round
from backend.app.utils.logging import get_logger

router = APIRouter(prefix="/predictions", tags=["predictions"])


def _settings_for_opportunities(db: Session, settings: Settings) -> Settings:
    """`get_settings()` es un singleton cacheado (`lru_cache`): nunca se
    muta en sitio (eso filtraria la preferencia de una request a todas las
    demas para siempre). La banda de cuotas de "Mejores oportunidades" SI
    es editable en runtime (`RankingConfig`/`/config/ranking`, seccion 7
    del pedido) -- esta funcion devuelve una COPIA de `settings` con esa
    preferencia aplicada, para que `rank_signals_v3` siempre vea la banda
    vigente (la guardada, o el default de `Settings` si nunca se guardo
    ninguna -- ver `get_opportunity_odds_band`)."""
    min_odds, max_odds = get_opportunity_odds_band(db, settings)
    return settings.model_copy(update={"min_opportunity_odds": min_odds, "max_opportunity_odds": max_odds})


logger = get_logger(__name__)


def serialize_prediction(
    prediction: Prediction,
    anomaly_flags: list[str] | None = None,
    is_best_prediction: bool = False,
    reliability: TrackRecordEstimate | None = None,
) -> dict:
    settings = get_settings()
    age_minutes = odds_age_minutes(prediction)
    is_stale = age_minutes is not None and age_minutes > (
        settings.max_odds_age_minutes or settings.opportunity_max_odds_age_minutes
    )
    return {
        "id": prediction.id,
        "match": prediction.match,
        "market": prediction.market,
        "line": prediction.line,
        "selection": prediction.selection,
        "model_probability": prediction.model_probability,
        "calibrated_probability": prediction.calibrated_probability,
        "market_probability": prediction.market_probability,
        "market_odds": prediction.market_odds,
        "fair_odds": prediction.fair_odds,
        "edge": prediction.edge,
        "expected_value": prediction.expected_value,
        "risk_adjusted_expected_value": prediction.risk_adjusted_expected_value,
        # "Mejores oportunidades" (cambio de filosofia del ranking):
        # EV_estimado = probabilidad CALIBRADA (o su fallback) * cuota - 1;
        # EV_conservador = probabilidad conservadora (min(calibrada,
        # uncertainty_low)) * cuota - 1 -- ver prediction/ranking.py.
        # Calculados aqui SOLO para mostrar (nunca se guardan en
        # `Prediction`: esta implementacion no toca los modelos
        # predictivos ni sus probabilidades, solo como se ordenan/
        # presentan). `None` sin cuota real, nunca inventados.
        "ev_calibrated": ev_with_probability(calibrated_probability_or_fallback(prediction), prediction.market_odds),
        "ev_conservative": ev_with_probability(conservative_probability(prediction), prediction.market_odds),
        "confidence": prediction.confidence,
        "data_quality": prediction.data_quality,
        "uncertainty_low": prediction.uncertainty_low,
        "uncertainty_high": prediction.uncertainty_high,
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
        "odds_origin_at": prediction.odds_origin_at,
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
        "odds_age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "is_stale_odds": is_stale,
        "anomaly_flags": anomaly_flags or [],
        "is_best_prediction": is_best_prediction,
        "reliability": (
            {
                "value": reliability.value,
                "source_level": reliability.source_level,
                "n_observations": reliability.n_observations,
            }
            if reliability is not None
            else None
        ),
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
        .filter(Match.status == "scheduled", ~Prediction.market.startswith("cards_"))
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


@router.get("/summary", response_model=SummaryOut)
def predictions_summary(db: Session = Depends(get_db)) -> dict:
    """Pedido de rediseño (seccion "resumen de jornada" del dashboard):
    fecha, partidos pendientes, competiciones disponibles, numero de
    oportunidades detectadas y numero de esas con datos de alta calidad.

    "Oportunidades detectadas" = predicciones de la JORNADA ACTUAL de
    cada competicion (mismo scope que /top-signals por defecto) que pasan
    el filtro de calidad Y no tienen contradiccion -- el mismo criterio
    real usado en toda la app para "esto es una señal", nunca un conteo
    aparte con su propio criterio inventado."""
    from backend.app.db.models.core import Competition

    settings = _settings_for_opportunities(db, get_settings())
    competitions = db.query(Competition).all()
    match_ids = _resolve_scope_match_ids(db, "current_round", None)
    matches_pending = (
        db.query(Match)
        .filter(
            Match.id.in_(match_ids),
            Match.status == "scheduled",
            Match.kickoff_utc >= dt.datetime.utcnow(),
        )
        .count()
        if match_ids
        else 0
    )

    candidates = _base_signal_query(db, None, True, scope="current_round").all()
    by_match = _group_by_match(candidates)
    # "Mejores oportunidades" (cambio de filosofia del ranking): el
    # conteo de "oportunidades detectadas" del resumen de jornada usa el
    # MISMO criterio real que /top-signals, nunca uno aparte inventado.
    track_records = market_track_records(db)
    included, _ = rank_signals_v3(candidates, settings, track_records)
    opportunities = [
        s
        for s in included
        if is_strong_signal(
            compute_anomaly_flags(s.prediction, by_match[s.prediction.match_id], settings)
        )
    ]
    high_quality = sum(
        1
        for s in opportunities
        if signal_tier(s.prediction.confidence, s.prediction.data_quality) == "HIGH_DATA_SUPPORT"
    )

    return {
        "date": dt.date.today(),
        "matches_pending": matches_pending,
        "competitions_count": len(competitions),
        "opportunities_count": len(opportunities),
        "high_quality_count": high_quality,
    }


@router.get("/current-round", response_model=CurrentRoundPredictionsOut)
def predictions_current_round(competition_code: str = Query(...), db: Session = Depends(get_db)) -> dict:
    """Predicciones de la JORNADA ACTUAL de una competicion (seccion 1/11 de
    la revision de arquitectura), no "los proximos partidos que haya" sin
    mas. Ver services/round_service.py para la definicion exacta de
    "jornada actual" y sus limitaciones honestas (fallback por fecha si
    los fixtures no tienen `matchday` todavia)."""
    round_info = get_current_round(db, competition_code)
    is_next_round = False
    # La jornada "actual" puede no tener YA ningun partido por jugar (todos
    # finalizados o con kickoff ya pasado, ver round_service.py) sin que
    # eso signifique que no hay nada que mostrar: la jornada siguiente ya
    # puede tener fecha. Pedido explicito del usuario: "quiero que me
    # enseñe los de la jornada siguiente" en vez de una portada vacia.
    if round_info is None or not round_info.match_ids:
        next_info = get_next_round(db, competition_code)
        if next_info is not None and next_info.match_ids:
            round_info = next_info
            is_next_round = True
    if round_info is None:
        return {"round": None, "predictions": []}

    predictions = (
        db.query(Prediction).filter(Prediction.match_id.in_(round_info.match_ids), ~Prediction.market.startswith("cards_")).all()
        if round_info.match_ids
        else []
    )
    # Bug real reportado por el usuario: esta ruta nunca marcaba
    # `is_best_prediction` (quedaba `False` por defecto para TODAS las
    # predicciones devueltas, aunque el partido si tuviera una señal
    # fuerte de verdad) -- el frontend (resumen "lo mas probable" de cada
    # partido en la portada) depende de esa bandera para saber cual
    # mostrar, igual que ya hace /matches/{id}/predictions. Mismo calculo,
    # reutilizado aqui.
    by_match = _group_by_match(predictions)
    best_by_match = best_prediction_per_match(
        by_match,
        edge_reliability_curve=fit_edge_reliability_curve(db),
    )
    return {
        "round": {
            "competition_code": competition_code,
            "round": round_info.round,
            "round_start": round_info.round_start,
            "round_end": round_info.round_end,
            "next_round": round_info.next_round,
            "is_fallback": round_info.is_fallback,
            "is_next_round": is_next_round,
            "match_ids": round_info.match_ids,
        },
        "predictions": [
            serialize_prediction(
                p,
                is_best_prediction=(
                    (best := best_by_match.get(p.match_id)) is not None and p.id == best.id
                ),
            )
            for p in predictions
        ],
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
    min_ev: float | None = None,
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
    query = db.query(Prediction).join(Match, Match.id == Prediction.match_id).filter(~Prediction.market.startswith("cards_"))
    if upcoming_only:
        query = query.filter(Match.status == "scheduled", Match.kickoff_utc > dt.datetime.utcnow(), Prediction.created_at < Match.kickoff_utc)
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
    if min_ev is not None:
        # Pedido real del usuario ("revision motor 2.0", Fase 6/8): filtro
        # por Expected Value minimo, como fraccion (0.10 = EV +10%) -- un
        # ajuste fino adicional, igual que `min_edge_pp`/`min_bookmakers`,
        # nunca sustituye al filtro duro de `evaluate_quality_gate`.
        query = query.filter(Prediction.expected_value >= min_ev)
    return query


def _group_by_match(predictions: list[Prediction]) -> dict[int, list[Prediction]]:
    """Agrupa por `match_id`, necesario para detectar contradicciones entre
    mercados del MISMO partido (ver prediction/anomaly.py) -- nunca se
    compara una prediccion contra las de otro partido."""
    from collections import defaultdict

    by_match: dict[int, list[Prediction]] = defaultdict(list)
    for prediction in predictions:
        by_match[prediction.match_id].append(prediction)
    return by_match


def _reliability_for(
    prediction: Prediction, provenance: dict[tuple[str, str], TrackRecordEstimate]
) -> TrackRecordEstimate | None:
    """Fiabilidad jerarquica (seccion 2.2) de esta prediccion concreta --
    ver market_track_records_with_provenance. Reutilizado por todos los
    endpoints que exponen `reliability` en la respuesta."""
    return provenance.get((prediction.match.competition.code, prediction.market))


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
    market_family: str | None = Query(None, description="'goals' o 'corners'"),
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
    min_ev: float | None = Query(
        None, description="Expected Value minimo, como fraccion (0.10 = EV +10%)"
    ),
    quality: str | None = QUALITY_QUERY,
    db: Session = Depends(get_db),
) -> list[dict]:
    """"Mejores oportunidades" (cambio de filosofia del ranking, pedido
    real del usuario, 2026-11: antes "mayor EDGE", ahora "mas probable
    con valor real") -- usa `rank_signals_v3` (ver prediction/ranking.py):
    SOLO cuotas reales entre `min_opportunity_odds`/`max_opportunity_odds`
    (1.35-1.90 por defecto, AMBOS limites inclusive, configurable en
    Configuracion -- ver RankingConfig), EV estimado Y conservador
    estrictamente positivos, y fiabilidad historica real y suficiente
    para ese (competicion, mercado). Ordenado por PRIORIDAD (nunca un
    score con pesos inventados): probabilidad calibrada primero, EV
    conservador despues, fiabilidad historica, incertidumbre, calidad de
    datos, y el edge como ultimo criterio informativo. Ademas, UNA sola
    predicción por partido (la mejor segun ese orden; todas las
    candidatas siguen guardadas, ver `select_one_per_match`). De la
    JORNADA ACTUAL de cada competicion por defecto (`scope=current_round`,
    ver `services/round_service.py`) -- nunca "un partido de la jornada
    siguiente porque tiene mas edge". Usa `scope=all_upcoming` para volver
    a una ventana de dias, o `date` para un dia concreto. Si ninguna
    predicción cumple todos los requisitos, la lista viene vacía (nunca
    se rellena con algo que no los cumpla).

    `sort_by`: el filtro de calidad (que decide QUE entra) es siempre el
    mismo; `sort_by` solo cambia el ORDEN dentro de lo que ya paso el
    filtro. "score" (por defecto) es el orden de prioridad de arriba;
    "edge" ordena de mayor a menor edge en puntos porcentuales en
    crudo -- util para ver la discrepancia modelo-mercado por separado,
    pero el edge sigue siendo solo informativo (nunca decide que entra).

    `min_fair_odds`/`max_fair_odds`: filtro por CUOTA JUSTA del modelo
    (no la de mercado), pedido explicito de usuario para acotar por
    rango (p.ej. "solo entre 1 y 2" = favoritos claros segun el modelo).

    `min_edge`/`min_bookmakers`/`min_ev`/`quality`: filtros ADICIONALES
    ajustables por request (pensados para sliders/selectores de frontend),
    nunca sustituyen al filtro DURO de calidad
    (`Settings`/`evaluate_quality_gate`, que ya exige un minimo de casas y
    edge no negativo por defecto) -- son un afinado extra sobre lo que ya
    paso ese filtro. `min_ev` es Expected Value minimo como fraccion
    (0.10 = EV +10%): un edge grande no implica EV positivo si la cuota es
    baja (ver prediction/edge.py).
    """
    settings = _settings_for_opportunities(db, get_settings())
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
        min_ev=min_ev,
    ).all()
    by_match = _group_by_match(candidates)
    track_records = market_track_records(db, competition_code)
    reliability_provenance = market_track_records_with_provenance(db, competition_code)
    included, excluded = rank_signals_v3(candidates, settings, track_records)
    if excluded:
        logger.info(
            "ranking.top_signals.excluded: total=%d reasons=%s",
            len(excluded),
            {r.reason.value: sum(1 for e in excluded if e.reason == r.reason) for r in excluded},
        )
    included = _filter_by_quality(included, quality, settings)
    # Seccion 2/18: una senhal CONTRADICTORIA (ambos lados de un grupo
    # mutuamente excluyente con model_probability > 0.5 para el mismo
    # partido; ver prediction/anomaly.py) nunca se muestra como "señal
    # fuerte", aunque su score compuesto sea alto.
    included = [
        s
        for s in included
        if is_strong_signal(compute_anomaly_flags(s.prediction, by_match[s.prediction.match_id], settings))
    ]
    if sort_by == "edge":
        included = sorted(included, key=lambda s: s.prediction.edge or 0.0, reverse=True)
    return [
        serialize_prediction(
            s.prediction,
            compute_anomaly_flags(s.prediction, by_match[s.prediction.match_id], settings),
            reliability=_reliability_for(s.prediction, reliability_provenance),
        )
        for s in included[:limit]
    ]


def _execution_assessments(db, candidates, settings, provenance, now):
    from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP

    by_match = _group_by_match(candidates)
    odds = defaultdict(list)
    for quote in db.query(MatchOdds).options(joinedload(MatchOdds.archived_quote)).filter(
        MatchOdds.match_id.in_(by_match), MatchOdds.snapshot_type == "pre_match"
    ):
        odds[quote.match_id].append(quote)

    return [
        (p, assess_opportunity(
            p, odds[p.match_id], ALL_MARKET_TO_ODDS_LOOKUP.get(p.market), settings, now,
            _reliability_for(p, provenance), compute_anomaly_flags(p, by_match[p.match_id], settings),
        ))
        for p in candidates
    ]


def _execution_details(assessment):
    quote, reference = assessment.quote, assessment.reference_quote
    return {
        "bookmaker": quote.bookmaker if quote else None,
        "quote_id": quote.history_quote_id if quote else None,
        "quote_price": reference.price if reference else None,
        "reference_bookmaker": reference.bookmaker if reference else None,
        "quote_origin": reference.provider_updated_at if reference else None,
        "quote_fetched": reference.fetched_at if reference else None,
        "max_age_minutes": assessment.max_age_minutes,
        "quote_age_minutes": assessment.age_minutes,
        "break_even": assessment.break_even,
        "edge": assessment.probability - assessment.break_even
        if assessment.break_even and valid_probability(assessment.probability) else None,
        "ev": assessment.ev,
        "conservative_ev": assessment.conservative_ev,
        "reasons": assessment.reasons,
        "confidence_reasons": assessment.confidence_reasons,
        "high_confidence": assessment.high_confidence,
        "score": assessment.score,
    }


@router.get("/available", response_model=list[AvailablePredictionOut])
def available_predictions(
    limit: int = Query(10, ge=1, le=30),
    per_match: int = Query(1, ge=1, le=3),
    scope: str = SCOPE_QUERY,
    competition_code: str | None = Query(None),
    days: int = Query(4, ge=1, le=30),
    date: dt.date | None = Query(None),
    min_fair_odds: float | None = Query(None, ge=1),
    max_fair_odds: float | None = Query(None, ge=1),
    min_edge: float | None = Query(None, ge=0),
    min_bookmakers: int | None = Query(None, ge=1),
    min_ev: float | None = Query(None),
    quality: str | None = QUALITY_QUERY,
    all_prices: bool = Query(False),
    value_only: bool = Query(False),
    opportunity_view: str | None = Query(None, pattern="^(main|high|explore)$"),
    db: Session = Depends(get_db),
):
    from backend.app.prediction.available_ranking import has_available_price, rank_available
    from backend.app.prediction.ranking import evaluate_quality_gate_v3

    settings = _settings_for_opportunities(db, get_settings())
    odds_band = (settings.min_opportunity_odds, settings.max_opportunity_odds)
    candidates = _base_signal_query(
        db, None, True, days, date, min_fair_odds, max_fair_odds, scope,
        competition_code, min_edge_pp=min_edge, min_bookmakers=min_bookmakers, min_ev=min_ev,
    ).all()
    if quality and quality.upper() != "ALL":
        candidates = [
            p for p in candidates
            if market_quality_tier(p.bookmakers_used, p.market_odds_min, p.market_odds_max,
                                   p.market_odds_median, settings) == quality.upper()
        ]
    by_match = _group_by_match(candidates)
    provenance = market_track_records_with_provenance(db, competition_code)
    reliability = {key: value.value for key, value in provenance.items()}
    if opportunity_view:
        pairs = _execution_assessments(db, candidates, settings, provenance, dt.datetime.utcnow())
        qualified = [(p, item) for p, item in pairs if not item.reasons]
        if opportunity_view == "high":
            qualified = [(p, item) for p, item in qualified if item.high_confidence]
        ranked = select_ranked(qualified, limit)
        if opportunity_view == "explore" and len(ranked) < limit:
            by_id = {p.id: item for p, item in pairs}
            included_matches = {p.match_id for p, _ in ranked}
            remaining = [p for p in candidates if p.match_id not in included_matches]
            ranked.extend((p, by_id[p.id]) for p in rank_available(
                remaining, reliability, per_match=per_match, limit=limit - len(ranked),
                all_prices=all_prices, prefer_calibrated=True, odds_band=odds_band,
            ))
        return [
            {
                **serialize_prediction(p, compute_anomaly_flags(p, by_match[p.match_id], settings),
                                       reliability=_reliability_for(p, provenance)),
                "has_statistical_value": not item.reasons,
                "value_exclusion": item.reasons[0] if item.reasons else None,
                "opportunity": _execution_details(item),
            }
            for p, item in ranked
        ]

    def assess(prediction):
        flags = compute_anomaly_flags(prediction, by_match[prediction.match_id], settings)
        reason = evaluate_quality_gate_v3(
            prediction, settings, reliability.get((prediction.match.competition.code, prediction.market))
        )
        return flags, reason, reason is None and is_strong_signal(flags)

    assessments = {
        p.id: assess(p) for p in candidates if has_available_price(p, all_prices=all_prices, odds_band=odds_band)
    }
    value_ids = {prediction_id for prediction_id, assessment in assessments.items() if assessment[2]}
    if value_only:
        candidates = [p for p in candidates if p.id in value_ids]

    rows = []
    for prediction in rank_available(
        candidates, reliability, per_match=per_match, limit=limit, all_prices=all_prices,
        value_ids=value_ids, prefer_calibrated=True, odds_band=odds_band,
    ):
        flags, reason, has_value = assessments[prediction.id]
        rows.append({
            **serialize_prediction(prediction, flags, reliability=_reliability_for(prediction, provenance)),
            "has_statistical_value": has_value,
            "value_exclusion": reason.value if reason else None,
        })
    return rows


@router.get("/best", response_model=list[PredictionOut])
def best_predictions(
    limit: int = Query(5, ge=1, le=50),
    market_family: str | None = Query(
        None, description="'goals' o 'corners'; omitir para mezclar todas"
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
    settings = _settings_for_opportunities(db, get_settings())
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
    by_match = _group_by_match(candidates)
    track_records = market_track_records(db, competition_code)
    reliability_provenance = market_track_records_with_provenance(db, competition_code)
    included, _ = rank_signals_v3(candidates, settings, track_records)
    included = _filter_by_quality(included, quality, settings)
    included = [
        s
        for s in included
        if is_strong_signal(compute_anomaly_flags(s.prediction, by_match[s.prediction.match_id], settings))
    ]
    return [
        serialize_prediction(
            s.prediction,
            compute_anomaly_flags(s.prediction, by_match[s.prediction.match_id], settings),
            reliability=_reliability_for(s.prediction, reliability_provenance),
        )
        for s in included[:limit]
    ]


@router.get("/best-of-day", response_model=BestOfDayOut)
def best_of_day(
    competition_code: str | None = Query(None, description="Restringe a una competicion; sin ella, las 5 del MVP"),
    db: Session = Depends(get_db),
) -> dict:
    """"Mejor señal del dia" (seccion 16 del brief): UNA sola prediccion (o
    ninguna), con el filtro MAS ESTRICTO del sistema -- deliberadamente mas
    exigente que `/predictions/best` (que devuelve un top-N con el filtro
    duro normal). Cada requisito de la seccion 16 es una linea explicita en
    `explanation`, nunca una condicion oculta:

    1. Mercado real (no MODEL_ONLY) -- `evaluate_quality_gate` ya lo exige.
    2. Cuota valida (no <=1.0, no absurda) -- `evaluate_quality_gate`.
    3. Bookmakers suficientes -- `evaluate_quality_gate` (min_bookmakers).
    4. Mercado reciente / sin cuotas obsoletas -- `evaluate_quality_gate`
       si `max_odds_age_minutes` esta configurado; en caso contrario se
       reporta la antiguedad igualmente (no se oculta la falta de este
       requisito).
    5. Matching fiable -- se apoya en que la prediccion ya viene de un
       `match_id` valido con equipos resueltos (no hay "partido fantasma"
       en este pipeline; ver `services/match_service.py`).
    6. Modelo calibrado disponible -- `calibrated_probability is not None`.
    7. Edge minimo -- el DOBLE del minimo global (`settings.min_edge_pp`),
       mas estricto a proposito para "la mejor del dia" (documentado aqui,
       no oculto).
    8. Sin contradiccion -- `prediction/anomaly.py::is_strong_signal`.
    """
    settings = _settings_for_opportunities(db, get_settings())
    candidates = _base_signal_query(
        db, None, True, None, None, None, None, "current_round", competition_code, None, None
    ).all()
    by_match = _group_by_match(candidates)
    track_records = market_track_records(db, competition_code)
    reliability_provenance = market_track_records_with_provenance(db, competition_code)
    included, _ = rank_signals_v3(candidates, settings, track_records)

    strict_min_edge = settings.min_edge_pp * 2
    checked: list[dict] = []
    winner = None
    for ranked in included:
        prediction = ranked.prediction
        flags = compute_anomaly_flags(prediction, by_match[prediction.match_id], settings)
        reasons_failed = []
        if not is_strong_signal(flags):
            reasons_failed.append("contradiccion detectada entre mercados del mismo partido")
        if prediction.calibrated_probability is None:
            reasons_failed.append("sin modelo calibrado disponible para este mercado")
        if prediction.edge is None or prediction.edge < strict_min_edge:
            reasons_failed.append(f"edge por debajo del minimo estricto ({strict_min_edge * 100:.1f}pp)")
        checked.append({"prediction_id": prediction.id, "passed": not reasons_failed, "reasons_failed": reasons_failed})
        if not reasons_failed and winner is None:
            winner = ranked

    if winner is None:
        return {
            "prediction": None,
            "explanation": [
                "Ninguna prediccion de la jornada actual cumple TODOS los requisitos minimos "
                "de 'mejor señal del dia' (mercado real, cuota valida, bookmakers suficientes, "
                "cuota reciente, modelo calibrado, edge minimo estricto, sin contradiccion).",
                f"Se evaluaron {len(checked)} candidatas que ya pasaban el filtro duro general.",
            ],
        }

    prediction = winner.prediction
    flags = compute_anomaly_flags(prediction, by_match[prediction.match_id], settings)
    serialized = serialize_prediction(
        prediction,
        flags,
        is_best_prediction=True,
        reliability=_reliability_for(prediction, reliability_provenance),
    )
    explanation = _build_explanation_summary(prediction, serialized) + [
        f"Edge minimo estricto exigido para 'mejor señal del dia': {strict_min_edge * 100:.1f} puntos porcentuales "
        f"(el doble del minimo general de {settings.min_edge_pp * 100:.1f}pp).",
        "Sin contradiccion con otro mercado del mismo partido (ver prediction/anomaly.py).",
        f"Probabilidad calibrada disponible: {prediction.calibrated_probability * 100:.1f}%."
        if prediction.calibrated_probability is not None
        else "Sin probabilidad calibrada (no deberia llegar aqui).",
    ]
    return {"prediction": serialized, "explanation": explanation}


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
    min_edge: float | None = Query(None, ge=0),
    min_bookmakers: int | None = Query(None, ge=1),
    min_ev: float | None = Query(None, ge=0),
    quality: str | None = QUALITY_QUERY,
    summary_only: bool = Query(False),
    execution: bool = Query(False),
    db: Session = Depends(get_db),
) -> dict:
    """Observabilidad (seccion 13): por cada prediccion candidata, si entro
    al ranking o no y por que. Pensado para depurar "por que esta senhal no
    aparece" sin tener que adivinar leyendo logs."""
    settings = _settings_for_opportunities(db, get_settings())
    candidates = _base_signal_query(
        db, market_family, upcoming_only, days, date, min_fair_odds, max_fair_odds, scope, competition_code,
        min_edge, min_bookmakers, min_ev=min_ev
    ).all()
    if execution:
        if quality and quality.upper() != "ALL":
            candidates = [p for p in candidates if market_quality_tier(
                p.bookmakers_used, p.market_odds_min, p.market_odds_max, p.market_odds_median, settings
            ) == quality.upper()]
        provenance = market_track_records_with_provenance(db, competition_code)
        pairs = _execution_assessments(db, candidates, settings, provenance, dt.datetime.utcnow())
        items = [item for _, item in pairs]
        qualified = [(p, item) for p, item in pairs if not item.reasons]
        by_market = defaultdict(lambda: {"qualified": 0, "high": 0, "confidence_reasons": Counter()})
        for prediction, item in qualified:
            market = by_market[prediction.market]
            market["qualified"] += 1
            market["high"] += item.high_confidence
            market["confidence_reasons"].update(item.confidence_reasons)
        summary = {
            "counts": {
                "matches": len({p.match_id for p in candidates}),
                "candidates": len(items),
                "candidates_with_valid_market": sum("sin_mercado" not in item.reasons for item in items),
                "candidates_in_band": sum(
                    "sin_mercado" not in item.reasons and "cuota_fuera_de_banda" not in item.reasons for item in items
                ),
                "usable_probabilities": sum(not any(
                    reason in item.reasons for reason in ("probabilidad_invalida", "calibracion_no_disponible", "calibracion_en_extremo",
                                   "ajuste_calibracion_sin_respaldo")
                ) for item in items),
                "positive_estimated_ev": sum(item.ev is not None and item.ev > 0 for item in items),
                "passed_gate": len(qualified),
                "high_confidence": sum(item.high_confidence for item in items),
                "opportunities": len(select_ranked(qualified, 10)),
            },
            "reasons": dict(Counter(reason for item in items for reason in item.reasons)),
            "by_market": dict(by_market),
            "first_reasons": dict(Counter(item.reasons[0] for item in items if item.reasons)),
            "min_odds": settings.min_opportunity_odds,
            "max_odds": settings.max_opportunity_odds,
        }
        if not summary_only:
            summary["candidates"] = [
                {"prediction_id": p.id, "match_id": p.match_id, "market": p.market,
                 "model_probability": p.model_probability if valid_probability(p.model_probability) else None,
                 "calibrated_probability": p.calibrated_probability if valid_probability(p.calibrated_probability) else None,
                 **_execution_details(item)} for p, item in pairs
            ]
        return summary

    track_records = market_track_records(db, competition_code)
    # Procedencia de la fiabilidad (seccion 2.2, "fiabilidad jerarquica"):
    # `track_records` (arriba) sigue siendo el dict plano que ya consumia
    # `rank_signals_v3` (nunca se toca su contrato) -- esto es SOLO para
    # que la respuesta de depuracion diga de donde sale cada fiabilidad
    # (especifica de la competicion, de su tier, o global de ese mercado),
    # nunca presentando un nivel generico como si fuera especifico.
    provenance = market_track_records_with_provenance(db, competition_code)

    def _reliability_info(prediction: Prediction) -> dict | None:
        estimate = _reliability_for(prediction, provenance)
        if estimate is None:
            return None
        return {
            "value": estimate.value,
            "source_level": estimate.source_level,
            "n_observations": estimate.n_observations,
        }

    included, excluded = rank_signals_v3(candidates, settings, track_records)
    by_match = _group_by_match(candidates)
    main_count = sum(
        is_strong_signal(compute_anomaly_flags(signal.prediction, by_match[signal.prediction.match_id], settings))
        for signal in _filter_by_quality(included, quality, settings)
    )
    observation_reasons = {
        "calibracion_no_disponible", "incertidumbre_no_disponible",
        "origen_cuota_no_verificado", "cuota_desactualizada",
        "fiabilidad_historica_insuficiente",
    }
    observations = [
        item for item in excluded
        if item.reason.value in observation_reasons and (item.prediction.expected_value or 0) > 0
    ]
    from backend.app.prediction.available_ranking import has_available_price

    quoted = [p for p in candidates if has_available_price(p, all_prices=True)]
    preferred = [p for p in quoted if has_available_price(p)]
    counts = {
        "matches_with_available_price": len({p.match_id for p in quoted}),
        "matches_with_preferred_price": len({p.match_id for p in preferred}),
        "candidates_with_available_price": len(quoted),
        "candidates_with_preferred_price": len(preferred),
        "matches": len(by_match),
        "matches_with_market": len({p.match_id for p in candidates if p.market_odds is not None}),
        "candidates": len(candidates),
        "candidates_with_valid_market": sum(
            p.market_odds is not None and p.market_odds > 1 and p.market_probability is not None
            for p in candidates
        ),
        "positive_estimated_ev": sum(p.expected_value is not None and p.expected_value > 0 for p in candidates),
        "passed_gate": len(candidates) - len(excluded),
        "opportunities": main_count,
        "observation": len(observations),
    }
    reasons = dict(Counter(item.reason.value for item in excluded))
    if summary_only:
        return {"counts": counts, "reasons": reasons}
    return {
        "counts": counts,
        "reasons": reasons,
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
                "reliability": _reliability_info(s.prediction),
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
                "reliability": _reliability_info(e.prediction),
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
    probability = calibrated_probability_or_fallback(prediction)
    label = "calibrada" if prediction.calibrated_probability is not None else "sin calibrar"
    lines = [f"Probabilidad del modelo ({label}): {probability * 100:.1f}%."]
    if prediction.calibrated_probability is not None:
        lines.append(f"Estimación original antes de calibrar: {prediction.model_probability * 100:.1f}%.")
    if prediction.market_probability is not None:
        lines.append(
            f"El mercado (consenso de {prediction.bookmakers_used or 0} casa"
            f"{'s' if (prediction.bookmakers_used or 0) != 1 else ''}) implica "
            f"{prediction.market_probability * 100:.1f}% de probabilidad."
        )
    else:
        lines.append("No hay cuota de mercado disponible para esta seleccion todavia.")
    if prediction.market_probability is not None:
        lines.append(f"Diferencia frente al consenso: {(probability - prediction.market_probability) * 100:+.1f} puntos porcentuales.")
    if prediction.market_odds is not None:
        lines.append(f"Cuota de mercado disponible: {prediction.market_odds:.2f}.")
    if probability > 0:
        lines.append(f"Cuota justa según esa probabilidad: {1 / probability:.2f}.")
    if serialized["market_quality"] is not None:
        lines.append(
            f"Calidad de mercado: {serialized['market_quality']} "
            f"({prediction.bookmakers_used or 0} casas respaldando el consenso)."
        )
    if serialized["odds_age_minutes"] is not None:
        lines.append(f"El dato de origen del proveedor tiene {round(serialized['odds_age_minutes'])} minutos.")
    lines.append(
        f"Calidad de datos del modelo (informacion disponible de ambos equipos): "
        f"{prediction.data_quality:.2f} sobre 1. Confianza del modelo: {prediction.confidence:.2f} sobre 1."
    )
    return lines


@router.get("/{prediction_id}/detail", response_model=SignalDetailOut)
def get_prediction_detail(prediction_id: int, db: Session = Depends(get_db)) -> dict:
    """Detalle completo de una senhal (seccion 16/17): desglose
    bookmaker-por-bookmaker con la diferencia de cada cuota respecto al
    consenso (y si esa casa se descarto como outlier, ver
    `market/consensus.py`), mas una explicacion en lenguaje llano de por
    que aparece esta senhal, trazable a los datos reales."""
    from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP

    prediction = db.get(Prediction, prediction_id)
    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediccion no encontrada")

    serialized = serialize_prediction(prediction)

    market_key = ALL_MARKET_TO_ODDS_LOOKUP.get(prediction.market)
    metadata = prediction.features_used or {}
    archive_ids = metadata.get("consensus_input_history_ids")
    bookmaker_odds = []
    fetched_at = None
    if market_key is not None:
        raw_market, line, selection = market_key
        source = MatchOddsHistory if archive_ids else MatchOdds
        rows = db.query(source).filter(
            source.match_id == prediction.match_id, source.market == raw_market, source.line == line,
            source.snapshot_type == "pre_match",
        )
        if archive_ids:
            rows = rows.filter(source.id.in_(archive_ids))
        market_rows = rows.all()
        outliers = metadata.get("consensus_outliers") if archive_ids else None
        fetched_at = max((row.fetched_at for row in market_rows if row.fetched_at), default=None) if archive_ids else None
        bookmaker_odds = sorted([
            {
                "bookmaker": row.bookmaker,
                "price": row.price,
                "snapshot_type": row.snapshot_type,
                "diff_from_consensus": row.price - prediction.market_odds if prediction.market_odds is not None else None,
                "is_outlier": row.bookmaker in outliers if outliers is not None else None,
            }
            for row in market_rows if row.selection == selection
        ], key=lambda r: r["price"])

    return {
        **serialized,
        "bookmaker_odds": bookmaker_odds,
        "odds_fetched_at": fetched_at,
        "bookmaker_odds_from_archive": bool(archive_ids),
        "explanation_summary": _build_explanation_summary(prediction, serialized),
    }


@router.get("/{prediction_id}", response_model=PredictionOut)
def get_prediction(prediction_id: int, db: Session = Depends(get_db)) -> dict:
    prediction = db.get(Prediction, prediction_id)
    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediccion no encontrada")
    return serialize_prediction(prediction)
