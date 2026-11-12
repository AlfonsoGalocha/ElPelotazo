"""Bug real reportado por el usuario: cada explicacion calculada (una por
prediccion) generaba 'UserWarning: X does not have valid feature names,
but SimpleImputer was fitted with feature names' -- miles de veces en los
logs de un `predict-upcoming` real. Causa: `explain_logistic_pipeline`
convertia la fila a un ndarray suelto (sin nombres de columna) antes de
pasarsela al imputer, que se habia entrenado con un DataFrame. Puro ruido
en los logs (sklearn compara por posicion igualmente, que aqui es
correcto), pero deberia desaparecer del todo."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from backend.app.prediction.explanation import explain_logistic_pipeline


def test_explain_logistic_pipeline_raises_no_sklearn_warnings():
    feature_cols = ["home_goals_for_avg_last5", "away_goals_for_avg_last5", "home_attack_strength"]
    rng = np.random.default_rng(7)
    X = pd.DataFrame(rng.normal(size=(40, len(feature_cols))), columns=feature_cols)
    y = pd.Series(rng.integers(0, 2, size=40).astype(bool))

    pipeline = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression())
    pipeline.fit(X, y)

    row = X.iloc[0]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        factors = explain_logistic_pipeline(pipeline, feature_cols, row, top_n=3)

    assert len(factors) == 3
    for factor in factors:
        assert factor["feature"] in feature_cols
        assert factor["direction"] in ("increases_probability", "decreases_probability")


def test_explain_logistic_pipeline_result_unaffected_by_missing_values():
    """El arreglo (pasar un DataFrame en vez de un ndarray al imputer) no
    debe cambiar el resultado para una fila con valores ausentes -- la
    imputacion por mediana sigue funcionando igual, solo sin el aviso."""
    feature_cols = ["home_goals_for_avg_last5", "away_goals_for_avg_last5"]
    X = pd.DataFrame(
        {
            "home_goals_for_avg_last5": [1.0, 2.0, 3.0, np.nan],
            "away_goals_for_avg_last5": [0.5, 1.5, 2.5, 3.5],
        }
    )
    y = pd.Series([True, False, True, False])

    pipeline = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression())
    pipeline.fit(X, y)

    row = pd.Series({"home_goals_for_avg_last5": np.nan, "away_goals_for_avg_last5": 1.0})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        factors = explain_logistic_pipeline(pipeline, feature_cols, row, top_n=2)

    assert {f["feature"] for f in factors} == set(feature_cols)
    home_factor = next(f for f in factors if f["feature"] == "home_goals_for_avg_last5")
    assert home_factor["raw_value"] is None  # NaN de verdad, nunca inventado
