"""Post-procesado de calibracion: Platt scaling e Isotonic regression.

Ambos se ajustan SOLO sobre un conjunto de validacion separado del de
entrenamiento del modelo base (nunca sobre el mismo split usado para ajustar
attack/defense o los coeficientes ML), para no inflar artificialmente la
calibracion medida despues en test.
"""

from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


class IsotonicCalibrator:
    name = "isotonic"

    def __init__(self) -> None:
        self._model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)

    def fit(self, raw_probabilities: np.ndarray, y_true: np.ndarray) -> IsotonicCalibrator:
        self._model.fit(raw_probabilities, y_true.astype(float))
        return self

    def transform(self, raw_probabilities: np.ndarray) -> np.ndarray:
        return self._model.predict(raw_probabilities)


class PlattCalibrator:
    name = "platt"

    def __init__(self) -> None:
        self._model = LogisticRegression()

    @staticmethod
    def _logit(p: np.ndarray) -> np.ndarray:
        p_clipped = np.clip(p, 1e-6, 1 - 1e-6)
        return np.log(p_clipped / (1 - p_clipped))

    def fit(self, raw_probabilities: np.ndarray, y_true: np.ndarray) -> PlattCalibrator:
        X = self._logit(raw_probabilities).reshape(-1, 1)
        self._model.fit(X, y_true)
        return self

    def transform(self, raw_probabilities: np.ndarray) -> np.ndarray:
        X = self._logit(raw_probabilities).reshape(-1, 1)
        return self._model.predict_proba(X)[:, 1]


CALIBRATORS = {"isotonic": IsotonicCalibrator, "platt": PlattCalibrator}
