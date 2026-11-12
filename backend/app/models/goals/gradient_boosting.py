"""Tercer candidato ML del ensemble de goles (seccion 7 de la revision de
arquitectura: "CatBoost/LightGBM como alternativa al LogisticRegression
actual, anadido al ensemble, nunca sustituyendolo a ciegas"). LightGBM por
mercado, igual cobertura que `MarketClassifierModel`
(models/goals/ml_classifier.py) para que sea un candidato valido en
cualquier mercado, incluidos los de un grupo mutuamente excluyente
(ahi compite contra `MultinomialGroupClassifier` igual que contra el
logistico independiente -- ver services/model_service.py).

No se asume que arboles de gradient boosting generalizan mejor que una
regresion logistica con pocas features bien diseñadas (dataset pequenho,
riesgo real de sobreajuste): se entrena SIEMPRE y se compara objetivamente
por log loss en el holdout real, nunca se usa a ciegas
(models/goals/model_selection.py::pick_best_candidate /
_choose_ml_source_per_group en model_service.py).

LightGBM maneja valores NaN de forma nativa (cada split decide a que lado
mandar los missing segun minimiza la perdida de entrenamiento) y es
invariante a la escala de las features -- a diferencia de
MarketClassifierModel, no necesita SimpleImputer ni StandardScaler.

Hiperparametros DELIBERADAMENTE conservadores: el dataset de este proyecto
es de miles de partidos por competicion, no millones, y por mercado la
clase positiva puede ser minoritaria (p.ej. "draw" ~25%) -- un arbol
profundo con muchas hojas sobreajusta ese tamanho de muestra mucho antes
que una logistica regularizada. Verificado empiricamente (no solo en
teoria): con hiperparametros mas agresivos (200 arboles, profundidad 4,
15 hojas) en un dataset sintetico de ~270 partidos de entrenamiento, el
modelo terminaba PEOR que adivinar al 50% en el holdout (log loss 0.71 vs
0.69) por puro sobreajuste; con estos (menos arboles, profundidad 2-3,
regularizacion L1/L2, subsample) bate claramente al azar (log loss ~0.51).
Independientemente de esto, `services/model_service.py` nunca usa este
modelo a ciegas: solo se sirve en produccion si gana el backtest contra
los demas candidatos en el holdout REAL de cada competicion.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier

from backend.app.features.goals import feature_columns
from backend.app.prediction.market_labels import MARKET_DEFINITIONS, label_for_market

_LGBM_PARAMS = dict(
    n_estimators=80,
    max_depth=3,
    num_leaves=8,
    learning_rate=0.04,
    min_child_samples=30,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_alpha=0.5,
    reg_lambda=0.5,
    random_state=42,
    verbosity=-1,
)


def _make_estimator() -> LGBMClassifier:
    return LGBMClassifier(**_LGBM_PARAMS)


class GradientBoostingMarketModel:
    """Interfaz compatible con `MarketClassifierModel`
    (`.fit`, `.predict_market_probabilities`, `.pipelines_`, `.feature_cols_`)
    salvo que `pipelines_` guarda el ESTIMATOR LightGBM directamente (sin
    envolverlo en un `sklearn.Pipeline`, ya que no hace falta Imputer ni
    Scaler) -- `prediction/predictor.py` distingue el caso al explicar via
    `hasattr(pipeline, "named_steps")` para elegir SHAP en vez del camino
    lineal de `explain_logistic_pipeline`."""

    name = "lightgbm_market_classifier"

    def __init__(self) -> None:
        self.feature_cols_: list[str] = []
        self.pipelines_: dict[str, object] = {}

    def fit(self, table: pd.DataFrame) -> GradientBoostingMarketModel:
        finished = table.dropna(subset=["home_goals", "away_goals"]).copy()
        self.feature_cols_ = feature_columns(finished)
        X = finished[self.feature_cols_]

        for market_key in MARKET_DEFINITIONS:
            y = label_for_market(finished, market_key)
            if y.nunique() < 2:
                continue
            self.pipelines_[market_key] = _make_estimator().fit(X, y)
        return self

    def predict_market_probabilities(self, table: pd.DataFrame) -> dict[str, np.ndarray]:
        X = table[self.feature_cols_]
        out: dict[str, np.ndarray] = {}
        for market_key, estimator in self.pipelines_.items():
            proba = estimator.predict_proba(X)
            classes = list(estimator.classes_)
            positive_idx = classes.index(True) if True in classes else classes.index(1)
            out[market_key] = proba[:, positive_idx]
        return out
