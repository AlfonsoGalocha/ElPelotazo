"""Explicabilidad de una prediccion (seccion 17 del brief).

Regla dura: las explicaciones SIEMPRE se derivan de numeros realmente
calculados (coeficientes del modelo, valores de features), nunca de texto
generado libremente. Si un factor no se puede calcular, no aparece.

Para el clasificador logistico (pipeline Imputer->Scaler->LogisticRegression)
la contribucion de cada feature a la prediccion de UNA fila es
    contribution_i = coef_i * standardized_value_i
que es exactamente lo que "explica" el logit de la regresion logistica.
Para el modelo estadistico (Dixon-Coles/Poisson) reportamos las features de
entrada mas relevantes por magnitud relativa (sin pretender causalidad).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_DISPLAY_NAMES = {
    "xg_for_avg_last5_difference": "xG combinado (forma reciente)",
    "goals_for_avg_last5_difference": "Diferencia de goles anotados (forma reciente)",
    "goals_against_avg_last5_difference": "Diferencia de goles concedidos (forma reciente)",
    "shots_for_avg_last5_difference": "Diferencia de volumen de tiros",
    "attack_strength_difference": "Diferencia de fuerza ofensiva (rating dinamico)",
    "defense_strength_difference": "Diferencia de fuerza defensiva (rating dinamico)",
    "home_advantage": "Ventaja de jugar en casa",
    "home_rest_days": "Descanso del equipo local",
    "away_rest_days": "Descanso del equipo visitante",
}


def _humanize(feature_name: str) -> str:
    """Fallback legible cuando no hay una traduccion curada en
    FEATURE_DISPLAY_NAMES: "home_goals_for_n_prior" -> "Home goals for n prior".
    Sigue siendo el nombre real de la feature (nunca se inventa una etiqueta),
    solo se formatea para lectura humana.
    """
    return feature_name.replace("_", " ").capitalize()


def display_name_for(feature_name: str) -> str:
    return FEATURE_DISPLAY_NAMES.get(feature_name, _humanize(feature_name))


def explain_logistic_pipeline(pipeline, feature_cols: list[str], row: pd.Series, top_n: int = 5) -> list[dict]:
    """Funciona tanto con LogisticRegression (coef_ shape (1, n_features))
    como con PoissonRegressor (coef_ shape (n_features,)) — ambos exponen
    coeficientes lineales sobre las features estandarizadas, asi que la
    contribucion (coef * valor_estandarizado) se interpreta igual."""
    imputer, scaler, estimator = pipeline.named_steps.values()
    raw_values = row[feature_cols].to_numpy(dtype=float).reshape(1, -1)
    imputed = imputer.transform(raw_values)
    scaled = scaler.transform(imputed)[0]
    coefs = np.asarray(estimator.coef_).reshape(-1)

    contributions = scaled * coefs
    order = np.argsort(-np.abs(contributions))[:top_n]

    factors = []
    for idx in order:
        feature_name = feature_cols[idx]
        factors.append(
            {
                "feature": feature_name,
                "display_name": display_name_for(feature_name),
                "contribution": float(contributions[idx]),
                "direction": "increases_probability" if contributions[idx] > 0 else "decreases_probability",
                "raw_value": None if pd.isna(row.get(feature_name)) else float(row.get(feature_name)),
            }
        )
    return factors


def explain_statistical_inputs(row: pd.Series, top_n: int = 5) -> list[dict]:
    """Para modelos sin coeficientes (Dixon-Coles): reporta las features de
    entrada con mayor magnitud absoluta entre las mas informativas conocidas."""
    candidates = list(FEATURE_DISPLAY_NAMES.keys())
    available = [(f, row[f]) for f in candidates if f in row and pd.notna(row[f])]
    available.sort(key=lambda pair: abs(pair[1]), reverse=True)

    factors = []
    for feature_name, value in available[:top_n]:
        factors.append(
            {
                "feature": feature_name,
                "display_name": display_name_for(feature_name),
                "contribution": None,
                "direction": "increases_probability" if value > 0 else "decreases_probability",
                "raw_value": float(value),
            }
        )
    return factors
