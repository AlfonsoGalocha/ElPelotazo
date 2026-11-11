"""Clasificador CONJUNTO por grupo mutuamente excluyente (ver
prediction/market_labels.py::MUTUALLY_EXCLUSIVE_GROUPS) -- la correccion de
RAIZ documentada como trabajo futuro en docs/architecture_audit.md (seccion
6, punto 6): en vez de un `LogisticRegression` independiente por mercado
(models/goals/ml_classifier.py::MarketClassifierModel), UN modelo por grupo,
de forma que las probabilidades del grupo suman exactamente 1 por
CONSTRUCCION del propio clasificador, no por una renormalizacion posterior.

- Grupo de 2 mercados (over_2_5/under_2_5): un LogisticRegression binario
  estandar ya da p y 1-p exactos -- es, matematicamente, el caso particular
  de "multinomial" con 2 clases. Se entrena UNA sola vez sobre la etiqueta
  del primer mercado del grupo; el segundo es su complemento exacto, nunca
  un modelo separado que pueda discrepar.
- Grupo de 3 mercados (home_win/draw/away_win): un unico
  LogisticRegression(multi_class="multinomial") sobre una columna
  categorica cuyas clases son literalmente los NOMBRES de los mercados
  ("home_win"/"draw"/"away_win"), auto-documentado via `estimator.classes_`.

Mercados que NO pertenecen a ningun grupo (btts, over_1_5, over_3_5,
double_chance derivado, team_over_*) no se entrenan aqui: no hay ninguna
restriccion de suma=1 que violar entre ellos, asi que seguir usando
`MarketClassifierModel` para esos es igual de correcto y no aporta nada
sustituirlo. `services/model_service.py` combina ambos modelos mercado a
mercado, comparando por backtest cual generaliza mejor antes de usarlo en
produccion (nunca una sustitucion a ciegas) -- ver `SelectedMarketClassifier`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from backend.app.features.goals import feature_columns
from backend.app.prediction.market_labels import MUTUALLY_EXCLUSIVE_GROUPS, label_for_market


def _make_pipeline():
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(max_iter=1000, C=1.0),
    )


class MultinomialGroupClassifier:
    """Solo cubre los mercados que pertenecen a algun grupo mutuamente
    excluyente -- `predict_market_probabilities` nunca devuelve una entrada
    para un mercado fuera de esos grupos (a diferencia de `MarketClassifierModel`,
    que cubre todos). `pipelines_`/`feature_cols_` siguen la misma forma que
    `MarketClassifierModel` para que `prediction/explanation.py` funcione
    igual; `class_label_for_explanation`/`invert_for_market` son la
    extension necesaria SOLO para este modelo (un pipeline puede servir a
    mas de un mercado)."""

    name = "multinomial_group_classifier"

    def __init__(self) -> None:
        self.feature_cols_: list[str] = []
        self.pipelines_: dict[str, object] = {}
        self._class_label_for_market: dict[str, object] = {}
        self._invert_for_market: dict[str, bool] = {}

    def fit(self, table: pd.DataFrame) -> MultinomialGroupClassifier:
        finished = table.dropna(subset=["home_goals", "away_goals"]).copy()
        self.feature_cols_ = feature_columns(finished)
        X = finished[self.feature_cols_]

        for group in MUTUALLY_EXCLUSIVE_GROUPS:
            if len(group) == 2:
                positive_market, complement_market = group
                y = label_for_market(finished, positive_market)
                if y.nunique() < 2:
                    continue
                pipeline = _make_pipeline().fit(X, y)
                self.pipelines_[positive_market] = pipeline
                self.pipelines_[complement_market] = pipeline
                self._invert_for_market[complement_market] = True
            else:
                y_cat = pd.Series(np.full(len(finished), "", dtype=object), index=finished.index)
                for market_key in group:
                    mask = label_for_market(finished, market_key).to_numpy()
                    y_cat.loc[mask] = market_key
                if y_cat.nunique() < 2:
                    continue
                pipeline = _make_pipeline().fit(X, y_cat)
                for market_key in group:
                    self.pipelines_[market_key] = pipeline
                    self._class_label_for_market[market_key] = market_key
        return self

    def predict_market_probabilities(self, table: pd.DataFrame) -> dict[str, np.ndarray]:
        X = table[self.feature_cols_]
        out: dict[str, np.ndarray] = {}
        proba_cache: dict[int, np.ndarray] = {}
        for market_key, pipeline in self.pipelines_.items():
            cache_key = id(pipeline)
            if cache_key not in proba_cache:
                proba_cache[cache_key] = pipeline.predict_proba(X)
            proba = proba_cache[cache_key]
            classes = list(pipeline.classes_)
            if market_key in self._class_label_for_market:
                idx = classes.index(self._class_label_for_market[market_key])
                out[market_key] = proba[:, idx]
            else:
                positive_idx = classes.index(True) if True in classes else classes.index(1)
                p_positive = proba[:, positive_idx]
                out[market_key] = 1.0 - p_positive if self._invert_for_market.get(market_key) else p_positive
        return out

    def class_label_for_explanation(self, market_key: str) -> object | None:
        return self._class_label_for_market.get(market_key)

    def invert_for_market(self, market_key: str) -> bool:
        return self._invert_for_market.get(market_key, False)
