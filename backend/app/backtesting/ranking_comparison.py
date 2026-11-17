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

Metodologia: para cada estrategia ("v1" = `signal_score` ya congelado,
"v2" = `signal_score_v2` recalculado ahora sobre los mismos campos
congelados), se ordenan TODAS las predicciones liquidadas por su score
descendente y se simula apostar 1 unidad a cada una de las primeras N (para
varios N, incluido "todas") -- si una formula de ranking es realmente mejor
que otra, el ROI/win-rate de su TOP-N pequenho deberia ser mayor que el de
"todas", y mayor que el de la otra formula para el mismo N. Si no lo es,
esa es la evidencia real que pide el usuario antes de cambiar nada en
produccion.

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

from dataclasses import dataclass, field

import numpy as np
from sqlalchemy.orm import Session

from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction, PredictionResult
from backend.app.models.calibration.metrics import brier_score, log_loss_score
from backend.app.prediction.ranking import signal_score_v2

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
    # CONGELADO en services/prediction_service.py en el momento real de
    # generar la prediccion -- nunca se recalcula con la formula de hoy.
    return prediction.signal_score or 0.0


def _score_v2(prediction: Prediction) -> float:
    return signal_score_v2(prediction)


def _probability_for_strategy(prediction: Prediction, strategy: str) -> float:
    if strategy == "v2" and prediction.calibrated_probability is not None:
        return prediction.calibrated_probability
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
