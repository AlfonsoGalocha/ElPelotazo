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
from dataclasses import dataclass

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
from backend.app.prediction.uncertainty import wilson_interval
from backend.app.prediction.ranking import (
    LONGSHOT_ODDS_THRESHOLD,
    fit_edge_reliability_curve_from_observations,
    fit_longshot_calibration_curve_from_observations,
    fit_odds_reliability_curve_from_observations,
)
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
                        settlement_status="won" if outcome else "lost",
                    )
                )
                created += 1
        db.flush()
    return created


def void_cancelled_predictions(db: Session, competition_code: str | None = None) -> int:
    """Partidos CANCELADOS (ver ingestion/api_football/fixtures_provider.py:
    CANC/ABD/AWD/WO) nunca van a producir un resultado real -- cualquier
    `Prediction` suya que siga sin `PredictionResult` se liquida como
    "void" (stake devuelto), nunca como "lost" (seria penalizar al modelo
    por algo que no tuvo nada que ver con su prediccion).

    A diferencia de `settle_finished_predictions`, aqui no hace falta
    `build_match_feature_table`: no hay resultado real que derivar
    (`actual_result=None`, `outcome=False` como relleno INERTE -- nunca
    se lee para metricas, `settlement_status="void"` es lo unico que
    importa, ver `real_performance_report`/`market_track_records` y las
    curvas de fiabilidad mas abajo, todas excluyen "void" explicitamente).

    Deliberadamente NO cubre "postponed": ese estado puede revertir a
    "scheduled" cuando la fuente reprograma el partido bajo el mismo id
    (ver fixtures_provider.py) -- anularlo aqui podria liquidar como
    "void" una prediccion que en realidad se juega mas tarde, violando la
    inmutabilidad (nunca se retoca una prediccion ya liquidada)."""
    query = db.query(Match).filter(Match.status == "cancelled")
    if competition_code:
        competition = db.query(Competition).filter_by(code=competition_code).one_or_none()
        if competition is None:
            return 0
        query = query.filter(Match.competition_id == competition.id)
    match_ids = [m.id for m in query.all()]
    if not match_ids:
        return 0

    already_settled_ids = {
        row[0]
        for row in db.query(PredictionResult.prediction_id)
        .join(Prediction, Prediction.id == PredictionResult.prediction_id)
        .filter(Prediction.match_id.in_(match_ids))
        .all()
    }
    predictions = [
        p
        for p in db.query(Prediction).filter(Prediction.match_id.in_(match_ids)).all()
        if p.id not in already_settled_ids
    ]
    created = 0
    for prediction in predictions:
        db.add(
            PredictionResult(
                prediction_id=prediction.id,
                outcome=False,
                actual_result=None,
                settled_at=dt.datetime.utcnow(),
                closing_line_value=None,
                settlement_status="void",
            )
        )
        created += 1
    db.flush()
    return created


def _is_void(result: PredictionResult) -> bool:
    return result.settlement_status == "void"


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


# Bloque 4 de la mision "plataforma profesional" (pedido real del
# usuario): "Una tasa de acierto del 80% con 5 apuestas no puede
# presentarse como evidencia solida" -- mismo criterio ya usado para
# fiabilidad historica/calibracion en el resto del proyecto
# (MIN_CALIBRATION_MATCHES=60, MIN_SETTLED_FOR_TRACK_RECORD=30).
MIN_SAMPLE_SIZE_FOR_RELIABLE_STATS = 30


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
    voided: dict[tuple[str, str, int], int] = defaultdict(int)
    for prediction, result, competition, _match in rows:
        key = (competition.code, prediction.market, prediction.model_version_id)
        # Predicciones "void" (partido cancelado, ver
        # void_cancelled_predictions) se EXCLUYEN de Brier/log loss/ROI --
        # contarlas como "lost" penalizaria al modelo por algo ajeno a su
        # prediccion. Se cuentan aparte (`n_void`) para que la muestra
        # nunca desaparezca en silencio.
        if _is_void(result):
            voided[key] += 1
            continue
        segments[key].append((prediction, result))

    report: dict = {"segments": []}
    # Solo se reporta un segmento con AL MENOS una liquidacion real
    # (won/lost): si una clave entera quedo solo con "void" (caso raro,
    # p.ej. una competicion suspendida del todo), sus `PredictionResult`
    # siguen guardados en la BD tal cual, simplemente no generan un
    # segmento aqui -- ninguna metrica (Brier/ROI/...) tiene sentido sin
    # ninguna liquidacion real de la que calcularla.
    for key, items in segments.items():
        comp_code, market_key, model_version = key
        n_void = voided.get(key, 0)

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

        # Pedido real del usuario (Bloque 4): "incluye intervalos de
        # confianza y numero de observaciones -- una tasa de acierto del
        # 80% con 5 apuestas no puede presentarse como evidencia solida".
        # `accuracy` = tasa de acierto real de las selecciones liquidadas
        # (fraccion de `y_true` verdaderas) -- intervalo de Wilson al 95%
        # (ver prediction/uncertainty.py, ya usado en el resto del
        # proyecto para el mismo tipo de proporcion con `n` variable).
        # `low_sample_size` nunca oculta el dato, solo lo marca
        # explicitamente para que la UI pueda avisar sin tener que
        # adivinar un umbral por su cuenta.
        accuracy = float(np.mean(y_true))
        accuracy_ci_low, accuracy_ci_high = wilson_interval(accuracy, len(y_true))

        report["segments"].append(
            {
                "competition_code": comp_code,
                "market": market_key,
                "model_version_id": model_version,
                "n_settled": len(items),
                "n_void": n_void,
                "accuracy": accuracy,
                "accuracy_confidence_interval_95": [accuracy_ci_low, accuracy_ci_high],
                "low_sample_size": len(items) < MIN_SAMPLE_SIZE_FOR_RELIABLE_STATS,
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
# insuficiente. Sigue siendo el MINIMO real para que un nivel de
# RESPALDO (tier/global) se considere fiable -- ver
# `TRACK_RECORD_SHRINKAGE_K` para el nivel EXACTO, que ya no usa este
# corte (mejora validada con backtest, ver test_evaluation_service.py).

# Mejora real pendiente de la mision (Bloque 2.2, "shrinkage bayesiano
# en vez del corte duro de 30 observaciones"), IMPLEMENTADA Y VALIDADA
# con backtest (ver `test_shrinkage_beats_hard_cutoff_...` en
# test_evaluation_service.py): antes, una competicion con 29
# liquidaciones exactas perdia el 100% de esa evidencia real y caia
# entera al nivel de respaldo (tier/global); con 30, usaba el 100% de
# la exacta y 0% del respaldo -- un salto discontinuo que no refleja
# cuanta confianza merece de verdad una muestra de 29 frente a una de 30
# (apenas distinta). Formula estandar de shrinkage Beta-Binomial /
# James-Stein (equivalente a un prior Beta centrado en el valor de
# respaldo con `k` pseudo-observaciones de peso):
#     shrunk = (n_exacto * valor_exacto + k * valor_respaldo) / (n_exacto + k)
# En `n_exacto=0` da el respaldo puro; en `n_exacto -> infinito` converge
# al valor exacto puro; en `n_exacto = k` es un 50/50 -- se reutiliza
# `MIN_SETTLED_FOR_TRACK_RECORD` como `k` (mismo orden de magnitud que el
# antiguo "minimo para confiar del todo", nunca un numero nuevo inventado
# sin justificacion). El nivel de RESPALDO en si sigue exigiendo el corte
# duro de siempre (`MIN_SETTLED_FOR_TRACK_RECORD`) -- mezclar con un
# respaldo ya poco fiable seria agravar el problema, no resolverlo.
TRACK_RECORD_SHRINKAGE_K = MIN_SETTLED_FOR_TRACK_RECORD


@dataclass
class TrackRecordEstimate:
    """Fiabilidad historica con PROCEDENCIA explicita -- nunca se debe
    mostrar/tratar un valor de `source_level` distinto de
    "competition_market" como si fuera especifico de esa competicion
    exacta (pedido real del usuario, mision "fiabilidad jerarquica")."""

    value: float
    # "competition_market" | "competition_market_shrunk" (mezclado con el
    # respaldo via shrinkage, ver TRACK_RECORD_SHRINKAGE_K) |
    # "market_tier" | "market_global"
    source_level: str
    n_observations: int


def _track_record_from_observations(pairs: list[tuple[bool, float]]) -> tuple[float, int] | None:
    if len(pairs) < MIN_SETTLED_FOR_TRACK_RECORD:
        return None
    return _raw_reliability(pairs)


def _raw_reliability(pairs: list[tuple[bool, float]]) -> tuple[float, int] | None:
    """Igual calculo que `_track_record_from_observations` pero SIN el
    corte minimo -- la base cruda que el shrinkage del nivel exacto
    necesita para poder usar aunque sea 1 liquidacion real (nunca 0,
    ECE no esta definido sin observaciones)."""
    if not pairs:
        return None
    y_true = np.array([outcome for outcome, _ in pairs])
    y_prob = np.array([prob for _, prob in pairs])
    ece = expected_calibration_error(y_true, y_prob)
    return float(np.clip(1.0 - ece, 0.0, 1.0)), len(pairs)


def _shrink_toward_backup(
    exact: tuple[float, int] | None, backup: tuple[float, int] | None, k: int = TRACK_RECORD_SHRINKAGE_K
) -> tuple[float, int] | None:
    """Combina la evidencia EXACTA (posiblemente escasa) con el RESPALDO
    jerarquico ya fiable, ponderando por el tamanho de muestra real --
    ver el comentario de `TRACK_RECORD_SHRINKAGE_K` para la formula."""
    if exact is None:
        return backup
    value_exact, n_exact = exact
    if backup is None:
        return exact
    value_backup, _ = backup
    shrunk_value = (n_exact * value_exact + k * value_backup) / (n_exact + k)
    return shrunk_value, n_exact


def market_track_records_with_provenance(
    db: Session, competition_code: str | None = None
) -> dict[tuple[str, str], TrackRecordEstimate]:
    """Fiabilidad historica JERARQUICA por (competicion, mercado) (pedido
    real del usuario, mision "oportunidades" seccion 2.2): antes, una
    combinacion con menos de `MIN_SETTLED_FOR_TRACK_RECORD` liquidaciones
    EXACTAS desaparecia del todo del ranking (`ExclusionReason.
    INSUFFICIENT_TRACK_RECORD`), aunque ese mismo mercado tuviera muchisimo
    historico en OTRAS competiciones de perfil similar -- causa real
    confirmada de que "Oportunidades" se quedara vacio en ligas/mercados
    con menos recorrido (ver docs/mission_continuity.md, Bloque 2.2).

    Jerarquia (actualizada, Bloque 2.2 de la mision -- shrinkage bayesiano
    en vez de un salto brusco todo-o-nada, ver `TRACK_RECORD_SHRINKAGE_K`
    y `_shrink_toward_backup`):
      1. (competicion, mercado) exacto con >= `MIN_SETTLED_FOR_TRACK_RECORD`
         liquidaciones: se usa puro, sin mezclar con nada (misma
         fiabilidad que antes).
      2. (competicion, mercado) exacto con 1-29 liquidaciones Y un
         respaldo fiable disponible: se MEZCLA con el respaldo (tier o
         global), ponderado por `n_exacto` real -- `source_level`
         "competition_market_shrunk" deja explicito que es una mezcla,
         nunca un valor puro de ningun nivel.
      3. (tier de competicion, mercado) -- ver `Competition.tier`
         ("CORE"/"EXPERIMENTAL", ya existente, seccion 26): agrupa
         competiciones de perfil similar en vez de inventar una
         taxonomia nueva. Puro, sin evidencia exacta que mezclar.
      4. (mercado) global, todas las competiciones. Puro, mismo caso.
      5. Ninguno disponible: la clave no aparece en el resultado (mismo
         comportamiento que antes -- nunca se inventa un valor).

    Usa SIEMPRE el historico COMPLETO de TODAS las competiciones para
    calcular los niveles 2/3 (nunca filtrado por `competition_code`):
    filtrar esa evidencia reduciria artificialmente la muestra de "mercado
    global" sin ninguna razon real. `competition_code` solo decide que
    CLAVES de salida se calculan, nunca que evidencia se usa.

    Cada estimacion guarda `source_level`/`n_observations` explicitos --
    quien consuma esto (p.ej. `/predictions/best/debug`) puede mostrar de
    donde sale la fiabilidad en vez de presentarla como si fuera siempre
    especifica de esa competicion exacta."""
    query = (
        db.query(Prediction, PredictionResult, Competition)
        .join(PredictionResult, PredictionResult.prediction_id == Prediction.id)
        .join(Match, Match.id == Prediction.match_id)
        .join(Competition, Competition.id == Match.competition_id)
    )
    by_exact: dict[tuple[str, str], list[tuple[bool, float]]] = defaultdict(list)
    by_tier: dict[tuple[str, str], list[tuple[bool, float]]] = defaultdict(list)
    by_market: dict[str, list[tuple[bool, float]]] = defaultdict(list)
    for prediction, result, competition in query.all():
        if _is_void(result):
            continue  # partido cancelado: no es evidencia de calibracion del mercado
        pair = (bool(result.outcome), prediction.model_probability)
        by_exact[(competition.code, prediction.market)].append(pair)
        tier = competition.tier or "CORE"
        by_tier[(tier, prediction.market)].append(pair)
        by_market[prediction.market].append(pair)

    competitions_query = db.query(Competition)
    if competition_code:
        competitions_query = competitions_query.filter(Competition.code == competition_code)
    all_market_keys = list(MARKET_DEFINITIONS) + list(SECONDARY_MARKET_DEFINITIONS)

    records: dict[tuple[str, str], TrackRecordEstimate] = {}
    for competition in competitions_query.all():
        tier = competition.tier or "CORE"
        for market_key in all_market_keys:
            # El RESPALDO (tier -> global) sigue exigiendo el corte duro
            # de siempre -- nunca mezclar con un nivel que el ya no es
            # fiable por si mismo.
            tier_level = _track_record_from_observations(by_tier.get((tier, market_key), []))
            backup = tier_level if tier_level is not None else _track_record_from_observations(
                by_market.get(market_key, [])
            )
            backup_source = "market_tier" if tier_level is not None else "market_global"

            exact_raw = _raw_reliability(by_exact.get((competition.code, market_key), []))
            exact_n = exact_raw[1] if exact_raw is not None else 0

            if exact_n >= MIN_SETTLED_FOR_TRACK_RECORD:
                # Ya pasa el minimo por si sola -- no hace falta mezclar
                # con nada, misma fiabilidad plena que antes.
                value, n = exact_raw
                records[(competition.code, market_key)] = TrackRecordEstimate(
                    value, "competition_market", n
                )
                continue

            if backup is None:
                if exact_raw is None:
                    continue  # ningun nivel tiene nada -- nunca se inventa (igual que antes)
                # Sin respaldo fiable: usar la exacta cruda es lo unico
                # posible, aunque la muestra sea pequenha (mismo riesgo
                # que aceptar cualquier nivel por debajo del minimo --
                # se prefiere a no tener ningun dato).
                value, n = exact_raw
                records[(competition.code, market_key)] = TrackRecordEstimate(
                    value, "competition_market", n
                )
                continue

            if exact_raw is None:
                # Sin ninguna liquidacion exacta todavia: respaldo puro,
                # exactamente igual que el comportamiento anterior.
                value, n = backup
                records[(competition.code, market_key)] = TrackRecordEstimate(value, backup_source, n)
                continue

            # Caso real que el corte duro resolvia mal: hay ALGO de
            # evidencia exacta (1-29 liquidaciones) y un respaldo fiable
            # -- se mezclan ponderando por `n_exacto` en vez de descartar
            # la exacta entera.
            value, n = _shrink_toward_backup(exact_raw, backup)
            records[(competition.code, market_key)] = TrackRecordEstimate(
                value, "competition_market_shrunk", n
            )
    return records


def market_track_records(
    db: Session, competition_code: str | None = None
) -> dict[tuple[str, str], float]:
    """ "1 - error de calibracion historico" por (competicion, mercado), para
    pasar directamente a prediction/ranking.py::rank_signals (parametro
    `market_track_records`) -- la pieza que docs/architecture_audit.md
    (seccion 4) deja pendiente de conectar: `signal_score` ya aceptaba este
    parametro desde esa sesion, pero nada lo calculaba con datos reales
    hasta ahora.

    Mismo CONTRATO de siempre (`dict[(competicion, mercado)] -> float`,
    sin procedencia) para no tocar ningun llamador existente
    (`prediction/ranking.py`, las rutas de `/predictions`) -- ahora el
    valor viene de la jerarquia de `market_track_records_with_provenance`
    en vez de exigir siempre el nivel mas especifico. Ver esa funcion para
    la version CON `source_level`/`n_observations` (usada por
    `/predictions/best/debug`)."""
    return {
        key: estimate.value
        for key, estimate in market_track_records_with_provenance(db, competition_code).items()
    }


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
    rows = [(p, r) for p, r in rows if not _is_void(r)]  # partido cancelado: no es evidencia real de edge
    if len(rows) < MIN_SETTLED_FOR_EDGE_RELIABILITY:
        return None

    edge_pp = np.array([p.edge * 100.0 for p, _r in rows])
    outcome = np.array([float(r.outcome) for _p, r in rows])
    return fit_edge_reliability_curve_from_observations(edge_pp, outcome)


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
    rows = [(p, r) for p, r in rows if not _is_void(r)]  # partido cancelado: no es evidencia real de cuota
    if len(rows) < MIN_SETTLED_FOR_ODDS_RELIABILITY:
        return None

    market_odds = np.array([p.market_odds for p, _r in rows])
    outcome = np.array([float(r.outcome) for _p, r in rows])
    return fit_odds_reliability_curve_from_observations(market_odds, outcome)


MIN_SETTLED_FOR_LONGSHOT_CALIBRATION = 30  # mismo criterio que los otros MIN_SETTLED_FOR_*


def fit_longshot_calibration_curve(
    db: Session, odds_threshold: float = LONGSHOT_ODDS_THRESHOLD
) -> IsotonicRegression | None:
    """Pedido explicito del usuario tras el rollback de emergencia del
    techo de cuota (seccion 14): "quiero que el modelo sea realista
    tambien con las predicciones, no solo [que mida] mucha diferencia de
    edge". El techo de cuota fallaba porque usaba el ROI PROMEDIO de un
    bucket entero (que hereda el vig del mercado, negativo en TODAS las
    cuotas observadas) como techo -- esto es distinto: recalibra la
    PROBABILIDAD del modelo (`model_probability -> resultado real`,
    `increasing=True`, igual direccion que una calibracion real), nunca
    un ROI, y SOLO en la cola de cuota alta
    (`market_odds >= odds_threshold`) donde la seccion 13 encontro
    sobreconfianza real:

        cuota 1-2:  model_prob 66.0% vs hit rate real 68.2% (bien calibrado)
        cuota 2-3:  model_prob 41.0% vs hit rate real 38.7%
        cuota 3-4:  model_prob 28.4% vs hit rate real 28.4% (bien calibrado)
        cuota 4-6:  model_prob 21.7% vs hit rate real 13.9%  (sobreconfianza clara)
        cuota 6+:   model_prob 15.8% vs hit rate real  3.6%  (sobreconfianza severa)

    Por que esto NO repite el error del techo de cuota: una curva de
    calibracion de probabilidad esta acotada en [0,1] y no mezcla el vig
    del mercado (nunca multiplica por `market_odds` hasta DESPUES, en
    `reality_capped_expected_value`) -- no hereda el sesgo "el ROI medio
    es negativo en todas partes" que colapsaba el ranking entero.

    Por que se restringe a la cola (`market_odds >= odds_threshold`) y
    no a todo el rango: la seccion 11.5 ya midio, con
    `ensemble_calibration_report` (walk-forward, sin fuga), que calibrar
    la probabilidad en el rango COMPLETO empeora Brier/ECE en los 4
    mercados centrales de LaLiga -- esta curva es deliberadamente mas
    estrecha, ajustada SOLO donde hay evidencia real de sobreconfianza,
    para no repetir ese resultado negativo.

    NUNCA activada en produccion todavia (ver
    `prediction/ranking.py::LONGSHOT_ODDS_THRESHOLD`): solo construida y
    testeada, pendiente de validacion walk-forward real en
    `backtesting/ranking_comparison.py` antes de conectarse a
    `signal_score_v2`.

    `None` si hay menos de `MIN_SETTLED_FOR_LONGSHOT_CALIBRATION`
    liquidaciones con cuota >= `odds_threshold` -- la cola tiene mucha
    menos muestra que el total (n=107 de 1133 en la evidencia real para
    cuota >=4), asi que este umbral puede alcanzarse mas tarde que los
    otros `MIN_SETTLED_FOR_*`.
    """
    rows = (
        db.query(Prediction, PredictionResult)
        .join(PredictionResult, PredictionResult.prediction_id == Prediction.id)
        .filter(Prediction.market_odds.isnot(None))
        .filter(Prediction.market_odds >= odds_threshold)
        .all()
    )
    rows = [(p, r) for p, r in rows if not _is_void(r)]  # partido cancelado: no es evidencia real de calibracion
    if len(rows) < MIN_SETTLED_FOR_LONGSHOT_CALIBRATION:
        return None

    model_probability = np.array([p.model_probability for p, _r in rows])
    outcome = np.array([float(r.outcome) for _p, r in rows])
    return fit_longshot_calibration_curve_from_observations(model_probability, outcome)
