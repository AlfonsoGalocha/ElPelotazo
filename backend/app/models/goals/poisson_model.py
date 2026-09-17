"""Modelo Poisson independiente: dos GLM Poisson (local y visitante) sobre las
features de forma/fuerza. Mas flexible que Dixon-Coles (usa features en vez
de solo team-strength), pero asume independencia entre goles local/visitante
(a diferencia de Dixon-Coles, que corrige la correlacion en marcadores bajos).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from backend.app.features.goals import feature_columns
from backend.app.models.base import GoalsModel, poisson_pmf_vector


class PoissonRegressionModel(GoalsModel):
    name = "poisson_regression"

    def __init__(self, alpha: float = 1.0) -> None:
        self.alpha = alpha
        self.feature_cols_: list[str] = []
        self.home_pipeline_ = None
        self.away_pipeline_ = None

    def _make_pipeline(self):
        return make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            PoissonRegressor(alpha=self.alpha, max_iter=500),
        )

    def fit(self, table: pd.DataFrame) -> PoissonRegressionModel:
        finished = table.dropna(subset=["home_goals", "away_goals"])
        self.feature_cols_ = feature_columns(finished)
        X = finished[self.feature_cols_]

        self.home_pipeline_ = self._make_pipeline().fit(X, finished["home_goals"])
        self.away_pipeline_ = self._make_pipeline().fit(X, finished["away_goals"])
        return self

    def predict_goal_distribution(self, table: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        X = table[self.feature_cols_]
        home_lam = self.home_pipeline_.predict(X)
        away_lam = self.away_pipeline_.predict(X)
        return poisson_pmf_vector(home_lam), poisson_pmf_vector(away_lam)
