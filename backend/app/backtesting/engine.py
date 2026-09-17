"""Motor de backtesting walk-forward.

Para cada fold (train=pasado acumulado, test=siguiente temporada):
1. Entrena el modelo SOLO con `train_index`.
2. Calcula la matriz de marcador conjunta para `test_index`
   (usando features que, por construccion, ya solo miran al pasado de cada
   partido individual — ver features/base.py).
3. Deriva probabilidad de mercado y la compara contra el resultado real.

Documenta explicitamente que features vio cada prediccion: `feature_set_version`
mas el propio corte temporal del fold garantizan trazabilidad (seccion 13).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backend.app.backtesting.metrics import (
    market_strategy_metrics,
    model_quality_metrics,
    performance_by_probability_bucket,
)
from backend.app.backtesting.splits import WalkForwardFold, expanding_window_splits
from backend.app.models.base import GoalsModel
from backend.app.prediction.market_labels import label_for_market
from backend.app.prediction.probability import market_probabilities


@dataclass
class BacktestFoldResult:
    fold: WalkForwardFold
    market: str
    y_true: np.ndarray
    y_prob: np.ndarray
    market_odds: np.ndarray | None


def run_walk_forward_backtest(
    table: pd.DataFrame,
    model_factory,
    market_key: str,
    min_train_seasons: int = 2,
    market_odds_col: str | None = None,
) -> list[BacktestFoldResult]:
    """`model_factory`: callable() -> GoalsModel nuevo (sin entrenar), para no
    reutilizar accidentalmente estado entre folds."""
    folds = expanding_window_splits(table, min_train_seasons=min_train_seasons)
    results = []

    for fold in folds:
        train_table = table.loc[fold.train_index]
        test_table = table.loc[fold.test_index]
        if test_table.empty or train_table.dropna(subset=["home_goals", "away_goals"]).empty:
            continue

        model: GoalsModel = model_factory()
        model.fit(train_table)

        probs = market_probabilities(model, test_table)[market_key]
        y_true = label_for_market(test_table, market_key).to_numpy()
        market_odds = test_table[market_odds_col].to_numpy() if market_odds_col else None

        results.append(BacktestFoldResult(fold, market_key, y_true, probs, market_odds))
    return results


def summarize_backtest(results: list[BacktestFoldResult]) -> dict:
    if not results:
        return {"n_predictions": 0}

    y_true = np.concatenate([r.y_true for r in results])
    y_prob = np.concatenate([r.y_prob for r in results])

    summary = {
        "market": results[0].market,
        "n_folds": len(results),
        "test_seasons": [r.fold.test_season for r in results],
        "model_quality": model_quality_metrics(y_true, y_prob),
        "performance_by_bucket": performance_by_probability_bucket(y_true, y_prob),
    }

    if all(r.market_odds is not None for r in results):
        market_odds = np.concatenate([r.market_odds for r in results])
        summary["market_strategy"] = market_strategy_metrics(y_true, y_prob, market_odds)
    else:
        summary["market_strategy"] = None

    return summary
