"""Explicabilidad para el candidato de arboles (LightGBM, ver
models/goals/gradient_boosting.py): a diferencia de LogisticRegression, un
LGBMClassifier no expone coeficientes lineales (`coef_`), asi que
prediction/explanation.py::explain_logistic_pipeline no aplica -- SHAP
(TreeExplainer, via la API unificada `shap.Explainer`) da la contribucion
REAL de cada feature a la prediccion de UNA fila concreta, mismo principio
que el resto de este modulo: nunca un texto inventado, siempre un numero
realmente calculado por el modelo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import shap

from backend.app.prediction.explanation import display_name_for

_explainer_cache: dict[int, object] = {}


def _explainer_for(estimator) -> object:
    """Un `shap.Explainer` por estimator entrenado (no por fila): construirlo
    recorre el modelo una vez, asi que se cachea por `id(estimator)` --
    todas las filas de una misma competicion/mercado comparten el mismo
    estimator entrenado."""
    key = id(estimator)
    if key not in _explainer_cache:
        _explainer_cache[key] = shap.Explainer(estimator)
    return _explainer_cache[key]


def explain_lightgbm_estimator(
    estimator, feature_cols: list[str], row: pd.Series, top_n: int = 5
) -> list[dict]:
    """`estimator`: un LGBMClassifier binario ya entrenado (clases False/True
    o 0/1). La contribucion de SHAP para un binario es, por convencion de la
    libreria, hacia la clase positiva -- consistente con como
    `explain_logistic_pipeline` interpreta sus propios coeficientes
    (positivo = aumenta la probabilidad de la seleccion del mercado)."""
    explainer = _explainer_for(estimator)
    raw_values = row[feature_cols].to_numpy(dtype=float).reshape(1, -1)
    explanation = explainer(raw_values)
    contributions = np.asarray(explanation.values).reshape(-1)

    order = np.argsort(-np.abs(contributions))[:top_n]
    factors = []
    for idx in order:
        feature_name = feature_cols[idx]
        contribution = contributions[idx]
        raw_value = row.get(feature_name)
        factors.append(
            {
                "feature": feature_name,
                "display_name": display_name_for(feature_name),
                "contribution": float(contribution),
                "direction": (
                    "increases_probability" if contribution > 0 else "decreases_probability"
                ),
                "raw_value": None if pd.isna(raw_value) else float(raw_value),
            }
        )
    return factors
