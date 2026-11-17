"""Backtest walk-forward del ENSEMBLE REAL (Dixon-Coles + clasificador ML +
pesos de mezcla aprendidos + calibracion), fold a fold -- nunca Dixon-Coles
aislado.

Hueco real encontrado en la revision "motor profesional 2.0" (2026-11):
`backtesting/engine.py::run_walk_forward_backtest` (el que usa el comando
`calibration-report`) solo sabe entrenar un `GoalsModel` suelto via
`model_factory()`, nunca el ensemble real que sirve produccion (que mezcla
Dixon-Coles con un clasificador ML, con pesos aprendidos por mercado -- ver
services/model_service.py::train_competition_models). Por eso nunca se
habia podido responder, con datos reales y sin asumir nada, si calibrar el
ENSEMBLE mejora algo: solo se habia probado calibrar Dixon-Coles aislado
(que si empeora un poco en LaLiga para home_win/draw/double_chance_1x,
pero eso no es lo que realmente ve el usuario en produccion).

Deliberadamente SEPARADO de model_service.py: es un instrumento de
DIAGNOSTICO puro (igual que backtesting/calibration.py ya lo es para
Dixon-Coles) -- nunca toca el pipeline de entrenamiento real ni el
artefacto que usa produccion, no cambia nada hasta que una decision
explicita y validada con estos resultados diga lo contrario.

Dentro de cada fold walk-forward (`train_seasons` = temporadas pasadas,
`test_season` = la siguiente, nunca vista):
1. La ULTIMA temporada de `train_seasons` se separa como holdout de
   SELECCION/CALIBRACION (mismo criterio que
   model_service.py::_pick_holdout_season, simplificado aqui a "siempre la
   ultima de train", nunca la de test) -- el resto ("fit_seasons") se usa
   para entrenar Dixon-Coles y los 3 candidatos ML, exactamente como en
   produccion.
2. En el holdout de seleccion (nunca visto por los modelos en el fit) se
   decide el ganador ML -- reutilizando `_choose_ml_source_per_group` de
   model_service.py (misma logica exacta de produccion, importada, no
   reimplementada a mano: duplicar esa heuristica a ojo seria el tipo de
   riesgo de divergencia silenciosa que este modulo existe para evitar) --
   se aprenden los pesos de ensemble y se ajustan los calibradores
   (isotonic Y platt, via el registro `CALIBRATORS`).
3. Se evalua en el fold de TEST (nunca visto en ningun paso anterior):
   probabilidad ensemble renormalizada cruda, calibrada-isotonic,
   calibrada-platt -- con los MISMOS pesos/calibradores aprendidos en el
   paso 2, nunca reajustados con el propio test.

Si `fit_seasons` queda vacio (fold inicial con solo 1 temporada de train)
o el holdout de seleccion/el fit no tienen partidos finalizados
suficientes, ese fold se SALTA (nunca se rellena con un numero inventado)
-- se reporta cuantos folds se usaron realmente.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backend.app.backtesting.splits import WalkForwardFold, expanding_window_splits
from backend.app.models.calibration.calibrators import CALIBRATORS
from backend.app.models.ensemble.ensemble import learn_ensemble_weights
from backend.app.models.goals.dixon_coles import DixonColesModel
from backend.app.models.goals.gradient_boosting import GradientBoostingMarketModel
from backend.app.models.goals.ml_classifier import MarketClassifierModel
from backend.app.models.goals.model_selection import pick_best_candidate
from backend.app.models.goals.multinomial_classifier import MultinomialGroupClassifier
from backend.app.prediction.market_labels import (
    MUTUALLY_EXCLUSIVE_GROUPS,
    label_for_market,
    renormalize_mutually_exclusive_groups,
)
from backend.app.prediction.probability import market_probabilities
from backend.app.services.model_service import MIN_CALIBRATION_MATCHES, _choose_ml_source_per_group

# Mismo criterio que model_service.py::MIN_CALIBRATION_MATCHES para el
# holdout de seleccion/calibracion -- importado de ahi (no redefinido a
# mano) para que ambos caminos exijan la misma cantidad de evidencia antes
# de confiar en un calibrador.
MIN_SELECTION_MATCHES = MIN_CALIBRATION_MATCHES


@dataclass
class EnsembleBacktestFoldResult:
    fold: WalkForwardFold
    market: str
    y_true: np.ndarray
    p_raw: np.ndarray  # probabilidad ensemble renormalizada, SIN calibrar
    # {"isotonic": ..., "platt": ...} -- ausente el metodo que no se pudo ajustar
    p_calibrated: dict[str, np.ndarray]
    market_odds: np.ndarray | None
    n_fit: int
    n_selection: int


def _group_for_market(market_key: str) -> tuple[str, ...]:
    for group in MUTUALLY_EXCLUSIVE_GROUPS:
        if market_key in group:
            return group
    return (market_key,)


def _finished(table: pd.DataFrame) -> pd.DataFrame:
    return table.dropna(subset=["home_goals", "away_goals"])


def _pick_ml_winner(
    group: tuple[str, ...],
    market_key: str,
    fit_table: pd.DataFrame,
    selection_finished: pd.DataFrame,
) -> tuple[str, object] | None:
    """Mismo arbol de decision que model_service.py::train_competition_models:
    grupo mutuamente excluyente -> ganador UNICO para todo el grupo (via
    `_choose_ml_source_per_group`); mercado suelto -> mejor candidato
    individual (via `pick_best_candidate`). Devuelve `None` si ningun
    candidato es valido (el mercado se queda solo con Dixon-Coles, igual
    que en produccion)."""
    candidate_models: dict[str, object] = {
        "logistic_independent": MarketClassifierModel().fit(fit_table),
        "multinomial_joint": MultinomialGroupClassifier().fit(fit_table),
        "gradient_boosting": GradientBoostingMarketModel().fit(fit_table),
    }
    if len(group) > 1:
        winners = _choose_ml_source_per_group(candidate_models, selection_finished)
        winner_name, winner_model = winners[market_key]
        return winner_name, winner_model

    y_true = label_for_market(selection_finished, market_key).to_numpy()
    candidates = [
        (name, model, model.predict_market_probabilities(selection_finished).get(market_key))
        for name, model in candidate_models.items()
        if name != "multinomial_joint"  # no cubre mercados fuera de un grupo
    ]
    winner = pick_best_candidate(y_true, candidates)
    if winner is None:
        return None
    return winner[0], winner[1]


def run_ensemble_walk_forward_backtest(
    table: pd.DataFrame,
    market_key: str,
    min_train_seasons: int = 2,
    market_odds_col: str | None = None,
    on_fold_start: Callable[[int, int, str], None] | None = None,
) -> list[EnsembleBacktestFoldResult]:
    """`on_fold_start(indice_1_based, total_folds, test_season)`: callback
    opcional, llamado ANTES de entrenar cada fold -- cada fold reentrena
    Dixon-Coles + 3 candidatos ML sobre datos reales, que puede tardar
    bastante mas que en el dataset sintetico de los tests; sin esta señal
    de progreso, el comando CLI parece colgado (bug real reportado por el
    usuario: Ctrl+C antes de que terminara)."""
    group = _group_for_market(market_key)
    folds = expanding_window_splits(table, min_train_seasons=min_train_seasons)
    results: list[EnsembleBacktestFoldResult] = []

    for fold_idx, fold in enumerate(folds, start=1):
        if on_fold_start is not None:
            on_fold_start(fold_idx, len(folds), fold.test_season)
        if len(fold.train_seasons) < 2:
            continue  # sin temporada de sobra para separar un holdout de seleccion/calibracion

        fit_seasons = fold.train_seasons[:-1]
        selection_season = fold.train_seasons[-1]
        fit_table = table[table["season_label"].isin(fit_seasons)]
        selection_table = table[table["season_label"] == selection_season]
        test_table = table.loc[fold.test_index]

        fit_finished = _finished(fit_table)
        selection_finished = _finished(selection_table)
        if (
            fit_finished.empty
            or len(selection_finished) < MIN_SELECTION_MATCHES
            or test_table.empty
        ):
            continue

        dixon_coles = DixonColesModel().fit(fit_table)
        winner = _pick_ml_winner(group, market_key, fit_finished, selection_finished)

        # PASO 2 (seleccion/calibracion): pesos de ensemble + calibradores,
        # para TODOS los mercados del grupo (una sola renormalizacion conjunta,
        # igual que produccion), nunca solo para `market_key` aislado.
        weights_by_market: dict[str, float] = {}
        p_ensemble_selection: dict[str, np.ndarray] = {}
        y_true_selection: dict[str, np.ndarray] = {}
        for m in group:
            p_dc_sel = market_probabilities(dixon_coles, selection_finished)[m]
            y_true_m = label_for_market(selection_finished, m).to_numpy()
            y_true_selection[m] = y_true_m
            if winner is not None:
                p_ml_sel = winner[1].predict_market_probabilities(selection_finished).get(m)
            else:
                p_ml_sel = None
            if p_ml_sel is not None:
                w, _ = learn_ensemble_weights([p_dc_sel, p_ml_sel], y_true_m)
                weights_by_market[m] = w[0]
                p_ensemble_selection[m] = w[0] * p_dc_sel + (1 - w[0]) * p_ml_sel
            else:
                p_ensemble_selection[m] = p_dc_sel

        p_ensemble_selection = renormalize_mutually_exclusive_groups(p_ensemble_selection)

        calibrators: dict[str, object] = {}
        p_sel_market = p_ensemble_selection[market_key]
        y_sel_market = y_true_selection[market_key]
        if len(set(y_sel_market.tolist())) >= 2:
            for method, calibrator_cls in CALIBRATORS.items():
                calibrators[method] = calibrator_cls().fit(
                    np.asarray(p_sel_market, dtype=float), y_sel_market
                )

        # PASO 3 (test, nunca visto antes): mismos pesos/calibradores del
        # paso 2, aplicados tal cual -- nunca reajustados aqui.
        p_ensemble_test: dict[str, np.ndarray] = {}
        for m in group:
            p_dc_test = market_probabilities(dixon_coles, test_table)[m]
            if winner is not None and m in weights_by_market:
                p_ml_test = winner[1].predict_market_probabilities(test_table).get(m)
                w = weights_by_market[m]
                p_ensemble_test[m] = w * p_dc_test + (1 - w) * p_ml_test
            else:
                p_ensemble_test[m] = p_dc_test
        p_ensemble_test = renormalize_mutually_exclusive_groups(p_ensemble_test)

        p_raw_test = np.asarray(p_ensemble_test[market_key], dtype=float)
        y_true_test = label_for_market(test_table, market_key).to_numpy()
        market_odds = (
            test_table[market_odds_col].to_numpy() if market_odds_col else None
        )

        p_calibrated: dict[str, np.ndarray] = {}
        for method, calibrator in calibrators.items():
            p_calibrated[method] = calibrator.transform(p_raw_test)

        results.append(
            EnsembleBacktestFoldResult(
                fold=fold,
                market=market_key,
                y_true=y_true_test,
                p_raw=p_raw_test,
                p_calibrated=p_calibrated,
                market_odds=market_odds,
                n_fit=len(fit_finished),
                n_selection=len(selection_finished),
            )
        )

    return results
