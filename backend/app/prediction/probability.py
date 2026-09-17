"""Deriva probabilidades de mercado a partir de cualquier GoalsModel.

Punto unico de conversion "modelo de goles" -> "probabilidad de mercado",
usado tanto en entrenamiento/backtesting como en prediccion en produccion,
para que ambos caminos sean identicos (evita skew train/serving).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.app.models.base import GoalsModel, joint_from_independent
from backend.app.prediction.market_labels import MARKET_DEFINITIONS, probability_from_score_matrix


def get_score_matrix(model: GoalsModel, table: pd.DataFrame) -> np.ndarray:
    """Usa la matriz conjunta propia del modelo si la ofrece (p.ej.
    Dixon-Coles con correccion rho); si no, asume independencia."""
    if hasattr(model, "predict_score_matrix"):
        return model.predict_score_matrix(table)
    home_dist, away_dist = model.predict_goal_distribution(table)
    return joint_from_independent(home_dist, away_dist)


def market_probabilities(model: GoalsModel, table: pd.DataFrame) -> dict[str, np.ndarray]:
    """Probabilidad de cada mercado del MVP para cada fila de `table`."""
    score_matrix = get_score_matrix(model, table)
    return {
        market_key: probability_from_score_matrix(score_matrix, market_key)
        for market_key in MARKET_DEFINITIONS
    }
