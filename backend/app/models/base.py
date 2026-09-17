"""Interfaz comun para modelos de goles.

Todo modelo, sea estadistico (Poisson/Dixon-Coles) o ML, debe poder producir
una distribucion completa de goles 0..5+ por equipo. A partir de esa
distribucion se derivan TODAS las probabilidades de mercado (over/under,
BTTS...) de forma consistente entre modelos, en `prediction/probability.py`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

MAX_GOALS = 5  # 0,1,2,3,4,5+ (5+ agrega la cola)


class GoalsModel(ABC):
    name: str

    @abstractmethod
    def fit(self, table: pd.DataFrame) -> GoalsModel:
        """`table`: salida de features.goals.build_match_feature_table, con
        home_goals/away_goals no nulos (solo partidos finalizados)."""

    @abstractmethod
    def predict_goal_distribution(self, table: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Devuelve (home_dist, away_dist), cada uno shape (n_rows, MAX_GOALS+1),
        con la probabilidad de anotar 0,1,...,MAX_GOALS-1,MAX_GOALS+ goles.
        Cada fila debe sumar 1 (o muy cerca, por redondeo)."""


def poisson_pmf_vector(lam: np.ndarray, max_goals: int = MAX_GOALS) -> np.ndarray:
    """Vectoriza la pmf de Poisson(lambda) para k=0..max_goals-1 y agrega la
    cola k>=max_goals en la ultima posicion, por fila de `lam` (shape (n,))."""
    from scipy.stats import poisson

    lam = np.clip(lam, 1e-6, None)
    n = lam.shape[0]
    dist = np.zeros((n, max_goals + 1))
    cumulative = np.zeros(n)
    for k in range(max_goals):
        pk = poisson.pmf(k, lam)
        dist[:, k] = pk
        cumulative += pk
    dist[:, max_goals] = np.clip(1.0 - cumulative, 0.0, 1.0)
    return dist


def joint_from_independent(home_dist: np.ndarray, away_dist: np.ndarray) -> np.ndarray:
    """Matriz de marcador conjunta bajo el supuesto de independencia entre
    goles local/visitante: producto exterior por fila.

    shape resultado: (n_rows, MAX_GOALS+1, MAX_GOALS+1).
    Usado como fallback para cualquier modelo que solo produzca marginales
    (baseline, Poisson por features, ML). Dixon-Coles anhade una correccion
    de correlacion para marcadores bajos y sobreescribe este comportamiento
    via `predict_score_matrix`.
    """
    return np.einsum("ij,ik->ijk", home_dist, away_dist)
