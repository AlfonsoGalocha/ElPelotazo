"""Modelo ML discriminativo: un clasificador por mercado (Over 1.5/2.5/3.5,
Under 2.5, BTTS) entrenado directamente sobre las features, sin pasar por una
distribucion de goles intermedia.

No asumimos que ML es mejor que el enfoque estadistico (seccion 10 del
brief): este modelo se compara objetivamente contra Dixon-Coles/Poisson en
backtesting (mismo Brier/Log Loss, mismos folds walk-forward).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from backend.app.features.goals import feature_columns
from backend.app.prediction.market_labels import MARKET_DEFINITIONS, label_for_market


class MarketClassifierModel:
    name = "logistic_market_classifier"

    def __init__(self) -> None:
        self.feature_cols_: list[str] = []
        self.pipelines_: dict[str, object] = {}

    def _make_pipeline(self):
        return make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(max_iter=1000, C=1.0),
        )

    def fit(self, table: pd.DataFrame) -> MarketClassifierModel:
        finished = table.dropna(subset=["home_goals", "away_goals"]).copy()
        self.feature_cols_ = feature_columns(finished)
        X = finished[self.feature_cols_]

        for market_key in MARKET_DEFINITIONS:
            y = label_for_market(finished, market_key)
            if y.nunique() < 2:
                continue  # no se puede entrenar un clasificador con una sola clase
            self.pipelines_[market_key] = self._make_pipeline().fit(X, y)
        return self

    def predict_market_probabilities(self, table: pd.DataFrame) -> dict[str, np.ndarray]:
        X = table[self.feature_cols_]
        out = {}
        for market_key, pipeline in self.pipelines_.items():
            proba = pipeline.predict_proba(X)
            classes = list(pipeline.classes_)
            positive_idx = classes.index(True) if True in classes else classes.index(1)
            out[market_key] = proba[:, positive_idx]
        return out
