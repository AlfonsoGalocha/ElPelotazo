"""Compara predicciones YA HECHAS contra el resultado REAL de partidos que
ya se jugaron ("cuando acabe la jornada, ver si el modelo acerto").

Por que esto no necesita infraestructura nueva de "aprendizaje online": los
modelos de este proyecto (Dixon-Coles/Poisson/clasificador ML) se
REENTRENAN por lotes desde cero cada vez que se llama a `train` (nunca
mantienen estado entre llamadas), leyendo TODOS los partidos ya jugados de
la BD. Asi que "que el modelo aprenda de la realidad" ya ocurre en cuanto
`update` trae los resultados reales de la jornada y se reentrena -- lo que
faltaba (ver `refresh_cache()` en `history_dataset.py`, bug real corregido
en el mismo cambio que este modulo) era que esos resultados reales
llegaran a la BD en absoluto, y esto: un informe explicito de "como de
bien predijo el modelo ANTES de saber el resultado", para poder verlo y
decidir con datos si hace falta ajustar algo (features, pesos del
ensemble, umbrales de ranking...) en vez de reentrenar a ciegas.

Las predicciones de un partido que ya se jugo NUNCA se borran cuando
termina (`generate_predictions_for_competition` solo borra/regenera las de
partidos SIN jugar): siguen en la BD tal y como se calcularon antes del
pitido inicial, listas para compararse contra el resultado real sin haber
tenido ninguna oportunidad de "hacer trampa" mirando el futuro.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

import datetime as dt

from backend.app.backtesting.metrics import (
    market_strategy_metrics,
    model_quality_metrics,
    performance_by_edge_bucket,
    performance_by_probability_bucket,
)
from sklearn.isotonic import IsotonicRegression

from backend.app.models.calibration.metrics import expected_calibration_error
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match, MatchOddsHistory
from backend.app.db.models.modeling import Prediction, PredictionResult
from backend.app.features.goals import build_match_feature_table
from backend.app.market.clv import compute_clv
from backend.app.market.consensus import compute_market_consensus
from backend.app.prediction.market_labels import MARKET_DEFINITIONS, label_for_market
from backend.app.prediction.secondary_markets import (
    SECONDARY_MARKET_DEFINITIONS,
    label_for_secondary_market,
)
from backend.app.services.match_service import load_matches_dataframe


def _label_for_any_market(table, market_key: str):
    if market_key in MARKET_DEFINITIONS:
        return label_for_market(table, market_key)
    if market_key in SECONDARY_MARKET_DEFINITIONS:
        return label_for_secondary_market(table, market_key)
    return None


def _closing_consensus_odds(db: Session, match_id: int, market_key: str) -> float | None:
    """Cuota de CONSENSO de cierre (ver market/clv.py) para (match_id,
    market_key): de `MatchOddsHistory` (append-only), toma la ULTIMA fila
    registrada por bookmaker (su precio mas cercano al pitido inicial) y
    calcula el consenso igual que en produccion (`market/consensus.py`).
    `None` si el mercado no tiene cuota real conocida (ver
    ALL_MARKET_TO_ODDS_LOOKUP) o si no hay historico registrado todavia
    para este partido (nunca inventado)."""
    from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP

    lookup = ALL_MARKET_TO_ODDS_LOOKUP.get(market_key)
    if lookup is None:
        return None
    market, line, selection = lookup

    rows = (
        db.query(MatchOddsHistory)
        .filter_by(match_id=match_id, market=market, line=line)
        .order_by(MatchOddsHistory.recorded_at.asc())
        .all()
    )
    if not rows:
        return None

    latest_by_bookmaker: dict[str, MatchOddsHistory] = {}
    for row in rows:
        latest_by_bookmaker[(row.bookmaker, row.selection)] = row
    closing_rows = list(latest_by_bookmaker.values())

    consensus = compute_market_consensus(closing_rows, market, line, selection)
    return consensus.market_odds


def settle_finished_predictions(db: Session, competition_code: str | None = None) -> int:
    """Fase 4: para cada `Prediction` de un partido YA FINALIZADO que
    todavia no tiene un `PredictionResult`, crea uno (nunca lo actualiza si
    ya existe -- inmutabilidad absoluta, seccion 8 del brief). Devuelve
    cuantas filas nuevas se crearon.

    CRITICO (no leakage / inmutabilidad): esta funcion NUNCA toca los
    campos predictivos de `Prediction` (`model_probability`, `edge`,
    `signal_score`, etc.) -- unicamente INSERTA una fila nueva en la tabla
    separada `prediction_results`, relacion 1-a-1 (`unique=True` en
    `prediction_id`). La prediccion en si es exactamente la que se genero
    ANTES del pitido inicial (ver docstring del modulo); liquidar solo le
    ANHADE el resultado observado, nunca la regenera ni la recalcula con
    las features/cuotas de HOY.
    """
    competitions = (
        [db.query(Competition).filter_by(code=competition_code).one_or_none()]
        if competition_code
        else db.query(Competition).all()
    )
    created = 0
    for competition in competitions:
        if competition is None:
            continue
        matches = load_matches_dataframe(db, competition.id)
        table = build_match_feature_table(matches)
        finished = table[table["home_goals"].notna()]
        if finished.empty:
            continue
        finished_match_ids = finished["match_id"].tolist()

        already_settled_ids = {
            row[0]
            for row in db.query(PredictionResult.prediction_id)
            .join(Prediction, Prediction.id == PredictionResult.prediction_id)
            .filter(Prediction.match_id.in_(finished_match_ids))
            .all()
        }
        predictions = [
            p
            for p in db.query(Prediction).filter(Prediction.match_id.in_(finished_match_ids)).all()
            if p.id not in already_settled_ids
        ]
        if not predictions:
            continue

        table_by_match_id = finished.set_index("match_id")
        by_market: dict[str, list[Prediction]] = defaultdict(list)
        for prediction in predictions:
            by_market[prediction.market].append(prediction)

        for market, preds in by_market.items():
            rows = table_by_match_id.loc[[p.match_id for p in preds]]
            y_true_series = _label_for_any_market(rows, market)
            if y_true_series is None:
                continue  # mercado desconocido: nunca se inventa un resultado
            y_true_by_match = dict(zip(rows.index, y_true_series))
            for prediction in preds:
                label = y_true_by_match[prediction.match_id]
                if pd.isna(label):
                    # Bug real reportado por el usuario (ver
                    # secondary_markets.py::has_real_count_data): este
                    # partido no tiene corners reales registrados (fuente
                    # sin esa estadistica para el) -- NUNCA se liquida
                    # como si "se hubiera cumplido menos de X", se deja
                    # pendiente hasta que (si algun dia) haya dato real.
                    continue
                outcome = bool(label)
                match_row = table_by_match_id.loc[prediction.match_id]
                home_goals = match_row["home_goals"]
                away_goals = match_row["away_goals"]
                actual_result = (
                    f"{int(home_goals)}-{int(away_goals)}"
                    if home_goals == home_goals and away_goals == away_goals  # not NaN
                    else None
                )
                closing_odds = _closing_consensus_odds(db, prediction.match_id, prediction.market)
                clv = compute_clv(prediction.market_odds, closing_odds)
                db.add(
                    PredictionResult(
                        prediction_id=prediction.id,
                        outcome=outcome,
                        actual_result=actual_result,
                        settled_at=dt.datetime.utcnow(),
                        closing_line_value=clv,
                    )
                )
                created += 1
        db.flush()
    return created


def evaluate_settled_predictions(db: Session, competition_code: str | None = None) -> dict:
    """Para cada (competicion, mercado) con partidos YA FINALIZADOS que
    tenian una prediccion guardada, calcula Brier score / log loss /
    calibracion (siempre) y ROI hipotetico (solo si habia cuota de mercado
    real, para no mezclar "el modelo predice bien" con "esta cuota concreta
    habria dado dinero" -- ver backtesting/metrics.py).
    """
    competitions = (
        [db.query(Competition).filter_by(code=competition_code).one_or_none()]
        if competition_code
        else db.query(Competition).all()
    )
    report: dict = {"competitions": {}}

    for competition in competitions:
        if competition is None:
            continue
        matches = load_matches_dataframe(db, competition.id)
        table = build_match_feature_table(matches)
        finished = table[table["home_goals"].notna()]
        if finished.empty:
            continue

        finished_match_ids = finished["match_id"].tolist()
        predictions = db.query(Prediction).filter(Prediction.match_id.in_(finished_match_ids)).all()
        if not predictions:
            continue

        by_market: dict[str, list[Prediction]] = defaultdict(list)
        for prediction in predictions:
            by_market[prediction.market].append(prediction)

        table_by_match_id = finished.set_index("match_id")
        market_reports: dict = {}
        for market, preds in by_market.items():
            rows = table_by_match_id.loc[[p.match_id for p in preds]]
            y_true_series = _label_for_any_market(rows, market)
            if y_true_series is None:
                continue  # mercado desconocido (no deberia pasar, pero nunca se inventa una etiqueta)
            # Ver secondary_markets.py::has_real_count_data: un partido sin
            # corners reales da NaN aqui, no False -- se excluye del
            # informe en vez de contarlo como "no se cumplio".
            valid_mask = y_true_series.notna().to_numpy()
            if not valid_mask.any():
                continue
            preds = [p for p, valid in zip(preds, valid_mask, strict=True) if valid]
            y_true = y_true_series.to_numpy()[valid_mask].astype(bool)
            y_prob = np.array([p.model_probability for p in preds])

            quality = model_quality_metrics(y_true, y_prob)

            has_odds = np.array([p.market_odds is not None for p in preds])
            strategy = None
            if has_odds.any():
                strategy = market_strategy_metrics(
                    y_true[has_odds],
                    y_prob[has_odds],
                    np.array([p.market_odds for p in preds if p.market_odds is not None]),
                )

            market_reports[market] = {
                "n_predictions": len(preds),
                "n_with_market_odds": int(has_odds.sum()),
                "model_quality": quality,
                "market_strategy": strategy,
            }

        if market_reports:
            report["competitions"][competition.code] = market_reports

    return report


def real_performance_report(
    db: Session,
    competition_code: str | None = None,
    market: str | None = None,
    model_version_id: int | None = None,
) -> dict:
    """Fase 5 (seccion 10/11 del brief): metricas de `backtesting/metrics.py`
    aplicadas contra predicciones REALMENTE liquidadas
    (`PredictionResult`, ver `settle_finished_predictions`), no contra un
    backtest sintetico. Segmentado por competicion/mercado/version de
    modelo (los filtros que recibe esta funcion); el desglose por rango de
    cuota/probabilidad/edge lo dan `performance_by_probability_bucket` y
    `performance_by_edge_bucket`, que ya vienen incluidos por segmento.

    Solo usa predicciones YA LIQUIDADAS (con `PredictionResult`) -- nunca
    mezcla predicciones pendientes de resolver, que no tienen resultado
    real todavia.
    """
    query = (
        db.query(Prediction, PredictionResult, Competition, Match)
        .join(PredictionResult, PredictionResult.prediction_id == Prediction.id)
        .join(Match, Match.id == Prediction.match_id)
        .join(Competition, Competition.id == Match.competition_id)
    )
    if competition_code:
        query = query.filter(Competition.code == competition_code)
    if market:
        query = query.filter(Prediction.market == market)
    if model_version_id:
        query = query.filter(Prediction.model_version_id == model_version_id)

    rows = query.all()
    segments: dict[tuple[str, str, int], list[tuple[Prediction, PredictionResult]]] = defaultdict(
        list
    )
    for prediction, result, competition, _match in rows:
        segments[(competition.code, prediction.market, prediction.model_version_id)].append(
            (prediction, result)
        )

    report: dict = {"segments": []}
    for (comp_code, market_key, model_version), items in segments.items():
        y_true = np.array([bool(r.outcome) for _p, r in items])
        y_prob = np.array([p.model_probability for p, _r in items])
        odds = np.array([p.market_odds if p.market_odds is not None else np.nan for p, _r in items])

        quality = model_quality_metrics(y_true, y_prob)
        has_odds = ~np.isnan(odds)
        strategy = (
            market_strategy_metrics(y_true[has_odds], y_prob[has_odds], odds[has_odds])
            if has_odds.any()
            else None
        )
        by_probability = performance_by_probability_bucket(y_true, y_prob)
        by_edge = performance_by_edge_bucket(y_true, y_prob, odds) if has_odds.any() else []

        report["segments"].append(
            {
                "competition_code": comp_code,
                "market": market_key,
                "model_version_id": model_version,
                "n_settled": len(items),
                "model_quality": quality,
                "market_strategy": strategy,
                "performance_by_probability_bucket": by_probability,
                "performance_by_edge_bucket": by_edge,
            }
        )

    report["segments"].sort(key=lambda s: (s["competition_code"], s["market"]))
    return report


MIN_SETTLED_FOR_TRACK_RECORD = 30  # mismo criterio que MIN_CALIBRATION_MATCHES
# en model_service.py: con menos liquidaciones que esto, el error de
# calibracion historico es ruido, no señal -- se prefiere "sin dato
# todavia" (ausente del dict) a inventar un numero sobre una muestra
# insuficiente.


def market_track_records(
    db: Session, competition_code: str | None = None
) -> dict[tuple[str, str], float]:
    """ "1 - error de calibracion historico" por (competicion, mercado), para
    pasar directamente a prediction/ranking.py::rank_signals (parametro
    `market_track_records`) -- la pieza que docs/architecture_audit.md
    (seccion 4) deja pendiente de conectar: `signal_score` ya aceptaba este
    parametro desde esa sesion, pero nada lo calculaba con datos reales
    hasta ahora.

    Usa SOLO predicciones ya liquidadas (`PredictionResult`), igual fuente
    de verdad que `/models/performance`, agregado aqui sobre TODAS las
    versiones de modelo de ese mercado: el track record es del TIPO de
    mercado (p.ej. "over_2_5" en general), no de una version concreta --
    coincide con como `signal_score` lo documenta ("rendimiento historico
    observado de ESTE TIPO de mercado").

    `ranking.py::rank_signals` sigue siendo una funcion PURA (sin acceso a
    BD): esta es la unica funcion que toca la BD, el llamador (capa de
    API/servicio) la invoca una vez por request y pasa el dict resultante.
    """
    query = (
        db.query(Prediction, PredictionResult, Competition)
        .join(PredictionResult, PredictionResult.prediction_id == Prediction.id)
        .join(Match, Match.id == Prediction.match_id)
        .join(Competition, Competition.id == Match.competition_id)
    )
    if competition_code:
        query = query.filter(Competition.code == competition_code)

    observations: dict[tuple[str, str], list[tuple[bool, float]]] = defaultdict(list)
    for prediction, result, competition in query.all():
        key = (competition.code, prediction.market)
        observations[key].append((bool(result.outcome), prediction.model_probability))

    records: dict[tuple[str, str], float] = {}
    for key, pairs in observations.items():
        if len(pairs) < MIN_SETTLED_FOR_TRACK_RECORD:
            continue
        y_true = np.array([outcome for outcome, _ in pairs])
        y_prob = np.array([prob for _, prob in pairs])
        ece = expected_calibration_error(y_true, y_prob)
        records[key] = float(np.clip(1.0 - ece, 0.0, 1.0))
    return records


MIN_SETTLED_FOR_EDGE_RELIABILITY = 30  # mismo criterio que MIN_SETTLED_FOR_TRACK_RECORD


def fit_edge_reliability_curve(db: Session) -> IsotonicRegression | None:
    """Evidencia real (pedido del usuario, "revision motor 2.0" -- caso
    Getafe-Atletico: un edge de +39.6pp seguia arriba del ranking porque
    `uncertainty_low` mide ruido de MUESTRA, no desacuerdo con el
    MERCADO): `scripts/debug_edge_vs_hitrate.py`, corrido por el usuario
    sobre 1133 predicciones liquidadas reales, mostro que el hit rate cae
    de forma practicamente monotona con el tamanho del edge (53.4% en
    0-5pp -> 41.4% en 5-10pp -> 31.8% en 10-15pp; ROI real NEGATIVO a
    partir de 5pp). Un edge mas grande, en este sistema, es señal de que
    el modelo probablemente se equivoco, no de mejor oportunidad.

    Se ajusta una `IsotonicRegression` (misma herramienta que
    `models/calibration/calibrators.py`, aqui con `increasing=False`
    porque la relacion real observada es DECRECIENTE) sobre
    (edge_en_puntos_porcentuales -> resultado real), agregado sobre TODAS
    las competiciones/mercados a la vez (asi se midio la evidencia: el
    patron se mantiene igual o mas fuerte con mercado bien respaldado) --
    nunca una curva por mercado, que fragmentaria la muestra. Isotonic
    (a diferencia de un corte por buckets a mano) agrupa los tramos con
    poca muestra dentro de la tendencia monotona general en vez de
    tratarlos como independientes, y extrapola de forma plana (nunca
    "mejora" artificialmente) mas alla del edge mas extremo observado.

    `None` si hay menos de `MIN_SETTLED_FOR_EDGE_RELIABILITY` liquidaciones
    con cuota real en total -- se prefiere "sin correccion" (neutro) a
    ajustar sobre ruido.
    """
    rows = (
        db.query(Prediction, PredictionResult)
        .join(PredictionResult, PredictionResult.prediction_id == Prediction.id)
        .filter(Prediction.market_odds.isnot(None))
        .filter(Prediction.edge.isnot(None))
        .all()
    )
    if len(rows) < MIN_SETTLED_FOR_EDGE_RELIABILITY:
        return None

    edge_pp = np.array([p.edge * 100.0 for p, _r in rows])
    outcome = np.array([float(r.outcome) for _p, r in rows])

    model = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=False, out_of_bounds="clip")
    model.fit(edge_pp, outcome)
    return model


MIN_SETTLED_FOR_ODDS_RELIABILITY = 30  # mismo criterio que los otros MIN_SETTLED_FOR_*


def fit_odds_reliability_curve(db: Session) -> IsotonicRegression | None:
    """Evidencia real (pedido del usuario, tras el fix de edge de la
    seccion 12: "victoria local" a cuota 5.50/6.00, con
    `model_probability` de 33.5%/28.3%, seguia siendo "mejor oportunidad"
    -- "una cuota 5 o una cuota 6 no es una buena prediccion"):
    `scripts/debug_odds_vs_hitrate.py`, corrido por el usuario sobre las
    mismas 1133 predicciones liquidadas reales, confirma un
    "favorite-longshot bias" claro en el propio sistema -- el modelo
    SOBRESTIMA sistematicamente su propia probabilidad en el extremo de
    cuota alta:

        cuota 1-2:  model_prob 66.0% vs hit rate real 68.2% (bien calibrado)
        cuota 2-3:  model_prob 41.0% vs hit rate real 38.7%
        cuota 3-4:  model_prob 28.4% vs hit rate real 28.4% (bien calibrado)
        cuota 4-6:  model_prob 21.7% vs hit rate real 13.9%  (ROI -34.3%)
        cuota 6+:   model_prob 15.8% vs hit rate real  3.6%  (ROI -77.9%)

    Es DISTINTO del problema de edge grande (seccion 12): esto ocurre
    incluso con edge MODESTO, es un problema de la propia CUOTA (no de
    cuanto discrepa del mercado).

    IMPORTANTE -- bug real corregido antes de desplegar esto (verificado
    EN VIVO: el caso Getafe match_id=61, model_probability=0.335, cuota
    5.50, seguia arriba del ranking casi sin penalizar): la PRIMERA
    version de esta funcion ajustaba la curva sobre
    `model_probability -> resultado`, pero la evidencia de arriba esta
    medida por CUOTA, no por probabilidad del modelo -- un
    `model_probability` de 33.5% aparece tanto en apuestas de cuota ~3
    (bien calibradas) como de cuota ~5.5 (mal calibradas); agrupar por
    probabilidad MEZCLA ambos casos y diluye la correccion justo donde
    mas hace falta. Se ajusta sobre `market_odds` directamente (la MISMA
    variable que agrupa `scripts/debug_odds_vs_hitrate.py`), con
    `increasing=False` (cuota mas alta -> hit rate real mas bajo, al
    reves que con la probabilidad).

    OJO: esto NO sustituye a `model_probability` para mostrar ni para
    rankear directamente (la seccion 11.5 ya mostro con
    `ensemble_calibration_report`, walk-forward y sin fuga de datos, que
    calibrar la probabilidad del ensemble per-mercado EMPEORA Brier/ECE
    en los 4 mercados centrales de LaLiga) -- se usa UNICAMENTE como una
    cota conservadora adicional sobre el EV, igual principio que
    `fit_edge_reliability_curve`, nunca para reemplazar la probabilidad
    que ve el usuario.

    `None` si hay menos de `MIN_SETTLED_FOR_ODDS_RELIABILITY`
    liquidaciones con cuota real en total.
    """
    rows = (
        db.query(Prediction, PredictionResult)
        .join(PredictionResult, PredictionResult.prediction_id == Prediction.id)
        .filter(Prediction.market_odds.isnot(None))
        .all()
    )
    if len(rows) < MIN_SETTLED_FOR_ODDS_RELIABILITY:
        return None

    market_odds = np.array([p.market_odds for p, _r in rows])
    outcome = np.array([float(r.outcome) for _p, r in rows])

    model = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=False, out_of_bounds="clip")
    model.fit(market_odds, outcome)
    return model
