"""Backtesting comparativo de estrategias de RANKING (Fase 7 de la revision
"motor profesional 2.0"): dado un conjunto de predicciones YA LIQUIDADAS
(con `PredictionResult` real), compara que hubiera pasado si el sistema
solo hubiera recomendado el TOP-N de cada ronda/estrategia de puntuacion.

Deliberadamente SEPARADO de `backtesting/engine.py`/`ensemble_engine.py`:
aqui NO hay que reentrenar nada ni preocuparse de walk-forward -- todos los
campos que necesita (`model_probability`, `calibrated_probability`,
`risk_adjusted_expected_value`, `uncertainty_low/high`, `confidence`,
`data_quality`, `market_odds`) ya estaban CONGELADOS en el momento real de
generar cada prediccion (`services/prediction_service.py`), y el resultado
real (`PredictionResult.outcome`) es posterior e independiente -- comparar
estrategias de ranking sobre estos datos no tiene ningun riesgo de leakage
porque ninguna de las dos formulas ve el resultado antes de puntuar.

Metodologia: para cada estrategia ("v1" = `signal_score`, "v2" =
`signal_score_v2`, AMBAS recalculadas aqui sobre los mismos campos
congelados -- nunca se lee `prediction.signal_score` directamente, ver
`_score_v1`: desde que v2 se activo en produccion esa columna congela
v2, no v1), se ordenan TODAS las predicciones liquidadas por su score
descendente y se simula apostar 1 unidad a cada una de las primeras N (para
varios N, incluido "todas") -- si una formula de ranking es realmente mejor
que otra, el ROI/win-rate de su TOP-N pequenho deberia ser mayor que el de
"todas", y mayor que el de la otra formula para el mismo N.

NOTA (2026-11): v2 ya es la formula de PRODUCCION (ver
docs/architecture_audit.md seccion 11, activada tras evidencia real de
`ensemble-calibration-report`/esta misma herramienta) -- esta
comparacion sigue siendo util para vigilar si una futura v3 mejora sobre
v2, o para verificar que v2 sigue rindiendo bien a medida que crece el
historico real.

Limitacion declarada: `signal_score_v2` se recalcula aqui SIN
`market_track_record` real (usa el valor neutro 1.0 para ambas formulas)
para no introducir una complicacion de leakage distinta (el track record
"correcto" en cada momento historico dependeria de que liquidaciones ya
existian ANTES de esa fecha, que este modulo no reconstruye) -- el
mecanismo de track record es identico en ambas formulas, asi que omitirlo
en los dos por igual no sesga la comparacion ENTRE estrategias, solo deja
fuera un factor que de todas formas no es el que se esta evaluando aqui.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction, PredictionResult
from backend.app.models.calibration.metrics import brier_score, log_loss_score
from backend.app.prediction.ranking import (
    LONGSHOT_ODDS_THRESHOLD,
    fit_edge_reliability_curve_from_observations,
    fit_longshot_calibration_curve_from_observations,
    signal_score,
    signal_score_v2,
)

TOP_N_VALUES: tuple[int, ...] = (5, 10, 20, 50, 100)


@dataclass
class StrategySlice:
    strategy: str
    top_n: str  # "5", "10", ... o "all"
    n_bets: int
    win_rate: float | None
    total_staked: float
    total_pnl: float
    roi: float | None
    max_drawdown: float
    brier_score: float | None
    log_loss: float | None


@dataclass
class RankingComparisonReport:
    n_settled_with_market: int
    slices: list[StrategySlice] = field(default_factory=list)


def _score_v1(prediction: Prediction) -> float:
    # RECALCULADO con la formula v1 (nunca leyendo `prediction.signal_score`
    # directamente): desde que v2 se activo en produccion
    # (services/prediction_service.py, ver docs/architecture_audit.md
    # seccion 11), esa columna congela v2 para las predicciones nuevas --
    # leerla aqui haria que "v1" y "v2" fueran literalmente el mismo
    # numero para cualquier prediccion reciente, rompiendo la comparacion.
    # Recalcular la formula v1 explicita mantiene la comparacion valida
    # SIEMPRE, sea cual sea la formula activa en produccion.
    return signal_score(prediction)


def _score_v2(prediction: Prediction) -> float:
    return signal_score_v2(prediction)


def _probability_for_strategy(prediction: Prediction, strategy: str) -> float:
    """Ambas estrategias usan `model_probability` (CRUDA), nunca
    `calibrated_probability` -- ver prediction/ranking.py::signal_score_v2
    para la evidencia real (ensemble_calibration_report contra LaLiga)
    que descarto preferir la calibrada. `strategy` se mantiene como
    parametro por si una futura estrategia SI la necesitara, no por
    diferenciar v1/v2 hoy."""
    del strategy  # sin diferencia hoy; ver docstring
    return prediction.model_probability


def _slice_metrics(
    strategy: str, top_n_label: str, predictions: list[Prediction], outcomes: dict[int, bool]
) -> StrategySlice:
    y_true = np.array([outcomes[p.id] for p in predictions], dtype=bool)
    y_prob = np.array([_probability_for_strategy(p, strategy) for p in predictions], dtype=float)
    odds = np.array([p.market_odds for p in predictions], dtype=float)

    pnl = np.where(y_true, odds - 1.0, -1.0)
    cumulative = np.cumsum(pnl)
    running_max = np.maximum.accumulate(cumulative) if len(cumulative) else np.array([])
    drawdown = cumulative - running_max

    n = len(predictions)
    return StrategySlice(
        strategy=strategy,
        top_n=top_n_label,
        n_bets=n,
        win_rate=float(np.mean(y_true)) if n else None,
        total_staked=float(n),
        total_pnl=float(pnl.sum()) if n else 0.0,
        roi=float(pnl.sum() / n) if n else None,
        max_drawdown=float(drawdown.min()) if n else 0.0,
        brier_score=brier_score(y_true, y_prob) if n else None,
        log_loss=log_loss_score(y_true, y_prob) if n else None,
    )


def compare_ranking_strategies(
    settled_predictions: list[tuple[Prediction, PredictionResult]],
    top_n_values: tuple[int, ...] = TOP_N_VALUES,
) -> RankingComparisonReport:
    """`settled_predictions`: pares (Prediction, PredictionResult) YA
    LIQUIDADOS. Se descartan aqui (no antes) las que no tengan cuota de
    mercado real -- sin eso no hay PnL que simular, nunca se inventa."""
    with_market = [(p, r) for p, r in settled_predictions if p.market_odds is not None]
    outcomes = {p.id: bool(r.outcome) for p, r in with_market}

    report = RankingComparisonReport(n_settled_with_market=len(with_market))
    if not with_market:
        return report

    for strategy, score_fn in (("v1", _score_v1), ("v2", _score_v2)):
        ranked = sorted((p for p, _ in with_market), key=score_fn, reverse=True)
        for n in top_n_values:
            top_slice = ranked[:n]
            if not top_slice:
                continue
            report.slices.append(_slice_metrics(strategy, str(n), top_slice, outcomes))
        report.slices.append(_slice_metrics(strategy, "all", ranked, outcomes))

    return report


def load_settled_predictions(
    db: Session, competition_code: str | None = None, market: str | None = None
) -> list[tuple[Prediction, PredictionResult]]:
    """Mismo query exacto que `evaluation_service.py::real_performance_report`
    (misma fuente de verdad, no una variante) -- solo predicciones YA
    LIQUIDADAS (con `PredictionResult`)."""
    query = (
        db.query(Prediction, PredictionResult)
        .join(PredictionResult, PredictionResult.prediction_id == Prediction.id)
        .join(Match, Match.id == Prediction.match_id)
        .join(Competition, Competition.id == Match.competition_id)
    )
    if competition_code:
        query = query.filter(Competition.code == competition_code)
    if market:
        query = query.filter(Prediction.market == market)
    return query.all()


def load_settled_predictions_with_kickoff(
    db: Session,
    competition_code: str | None = None,
    market: str | None = None,
    min_kickoff: dt.datetime | None = None,
    max_kickoff: dt.datetime | None = None,
) -> list[tuple[Prediction, PredictionResult, dt.datetime]]:
    """Igual que `load_settled_predictions`, pero con `Match.kickoff_utc`
    incluido -- necesario para `compare_ranking_strategies_walk_forward`,
    que debe ordenar cronologicamente antes de poder ajustar cada curva
    SOLO con lo anterior a cada punto evaluado.

    `min_kickoff`/`max_kickoff` (seccion 15.10): partir el historico por
    fecha -- p.ej. para comprobar si una diferencia entre estrategias es
    estable en el tiempo o es un artefacto de un tramo concreto (un
    modelo mas viejo, una liga con menos partidos liquidados todavia)."""
    query = (
        db.query(Prediction, PredictionResult, Match.kickoff_utc)
        .join(PredictionResult, PredictionResult.prediction_id == Prediction.id)
        .join(Match, Match.id == Prediction.match_id)
        .join(Competition, Competition.id == Match.competition_id)
    )
    if competition_code:
        query = query.filter(Competition.code == competition_code)
    if market:
        query = query.filter(Prediction.market == market)
    if min_kickoff is not None:
        query = query.filter(Match.kickoff_utc >= min_kickoff)
    if max_kickoff is not None:
        query = query.filter(Match.kickoff_utc < max_kickoff)
    return query.all()


# Cada cuantas liquidaciones nuevas se reajustan las curvas dentro del
# walk-forward: reajustar en CADA fila seria fiel pero carisimo sin
# necesidad (IsotonicRegression es barato, pero son miles de ajustes);
# reajustar cada N imita ademas como funciona produccion de verdad (la
# curva se reajusta por REQUEST, no por prediccion individual) sin dejar
# de ser walk-forward real: ninguna liquidacion usada para evaluar entra
# jamas en la curva que la puntua.
WALK_FORWARD_REFIT_EVERY = 25

WALK_FORWARD_STRATEGIES: tuple[str, ...] = ("v1", "v2", "v2_edge_cap", "v2_edge_cap_longshot")


def compare_ranking_strategies_walk_forward(
    settled_with_kickoff: list[tuple[Prediction, PredictionResult, dt.datetime]],
    min_train_n: int = 30,
    top_n_values: tuple[int, ...] = TOP_N_VALUES,
) -> RankingComparisonReport:
    """Mismo proposito que `compare_ranking_strategies`, pero para
    estrategias que SI dependen de una curva ajustada sobre liquidaciones
    (`v2_edge_cap` = produccion actual, seccion 12;
    `v2_edge_cap_longshot` = candidata pendiente de validar, seccion 15)
    -- a diferencia de v1/v2 (formulas sin ningun parametro ajustado,
    donde `compare_ranking_strategies` ya es valido sin walk-forward),
    aqui ajustar la curva con el conjunto COMPLETO seria leakage de
    validacion: la curva "veria" el resultado de la propia prediccion
    que esta puntuando.

    Metodologia real: se ordena TODO cronologicamente por
    `Match.kickoff_utc`, y cada prediccion se puntua SIEMPRE con una
    curva ajustada SOLO con liquidaciones de partidos anteriores al suyo
    (nunca con el futuro, nunca con ella misma) -- las primeras
    `min_train_n` liquidaciones nunca se evaluan (no hay "antes" todavia
    con el minimo exigido por `fit_edge_reliability_curve`/
    `fit_longshot_calibration_curve`), exactamente como
    `backtesting/ensemble_engine.py` excluye los folds sin
    `min_train_seasons` suficientes.
    """
    with_market = [
        (p, r, k) for p, r, k in settled_with_kickoff if p.market_odds is not None
    ]
    with_market.sort(key=lambda row: row[2])  # cronologico, nunca por id/orden de insercion
    outcomes = {p.id: bool(r.outcome) for p, r, _k in with_market}

    report = RankingComparisonReport(n_settled_with_market=len(with_market))
    if len(with_market) <= min_train_n:
        return report  # ni un solo punto queda fuera de la ventana de entrenamiento minima

    scores: dict[str, dict[int, float]] = {name: {} for name in WALK_FORWARD_STRATEGIES}
    evaluated: list[Prediction] = []

    prior_edge_pp: list[float] = []
    prior_edge_outcome: list[float] = []
    prior_longshot_probability: list[float] = []
    prior_longshot_outcome: list[float] = []
    edge_curve = None
    longshot_curve = None

    for i, (prediction, result, _kickoff) in enumerate(with_market):
        if i >= min_train_n:
            if edge_curve is None or i % WALK_FORWARD_REFIT_EVERY == 0:
                edge_curve = (
                    fit_edge_reliability_curve_from_observations(
                        np.array(prior_edge_pp), np.array(prior_edge_outcome)
                    )
                    if len(prior_edge_pp) >= min_train_n
                    else None
                )
                longshot_curve = (
                    fit_longshot_calibration_curve_from_observations(
                        np.array(prior_longshot_probability), np.array(prior_longshot_outcome)
                    )
                    if len(prior_longshot_probability) >= min_train_n
                    else None
                )

            evaluated.append(prediction)
            scores["v1"][prediction.id] = signal_score(prediction)
            scores["v2"][prediction.id] = signal_score_v2(prediction)
            scores["v2_edge_cap"][prediction.id] = signal_score_v2(
                prediction, edge_reliability_curve=edge_curve
            )
            scores["v2_edge_cap_longshot"][prediction.id] = signal_score_v2(
                prediction,
                edge_reliability_curve=edge_curve,
                longshot_calibration_curve=longshot_curve,
            )

        # Esta liquidacion pasa a formar parte del "pasado" SOLO despues
        # de puntuarse (si le tocaba) -- nunca antes, o se puntuaria con
        # una curva que ya la conoce.
        if prediction.edge is not None:
            prior_edge_pp.append(prediction.edge * 100.0)
            prior_edge_outcome.append(float(result.outcome))
        if prediction.market_odds >= LONGSHOT_ODDS_THRESHOLD:
            prior_longshot_probability.append(prediction.model_probability)
            prior_longshot_outcome.append(float(result.outcome))

    if not evaluated:
        return report

    for strategy in WALK_FORWARD_STRATEGIES:
        strategy_scores = scores[strategy]
        ranked = sorted(evaluated, key=lambda p: strategy_scores[p.id], reverse=True)
        for n in top_n_values:
            top_slice = ranked[:n]
            if not top_slice:
                continue
            report.slices.append(_slice_metrics(strategy, str(n), top_slice, outcomes))
        report.slices.append(_slice_metrics(strategy, "all", ranked, outcomes))

    return report
