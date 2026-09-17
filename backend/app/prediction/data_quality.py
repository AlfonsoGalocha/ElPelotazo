"""Data Quality Score: independiente de la confianza del modelo (seccion 39).

Mide cuanta informacion tenia el modelo disponible para esta prediccion
concreta, NO si el modelo acierta o esta bien calibrado. Un modelo puede
tener datos excelentes y aun asi equivocarse; y puede tener pocos datos y
aun asi acertar por azar. Nunca se mezclan estas dos nociones.

Formula (documentada, no arbitraria):
    data_quality = mean(
        completeness,      # fraccion de features clave no-missing
        sample_size_score, # partidos previos disponibles / umbral objetivo
        freshness_score,   # inverso de dias desde el ultimo partido conocido
    )
Cada componente en [0, 1].
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TARGET_SAMPLE_SIZE = 10  # partidos previos "suficientes" por equipo (ventana principal)
MAX_STALE_DAYS = 21  # a partir de aqui, freshness_score satura a 0


def completeness_score(row: pd.Series, key_features: list[str]) -> float:
    if not key_features:
        return 0.0
    present = sum(1 for f in key_features if f in row and pd.notna(row[f]))
    return present / len(key_features)


def sample_size_score(n_prior_home: float, n_prior_away: float) -> float:
    avg_n = np.nanmean([n_prior_home, n_prior_away])
    if np.isnan(avg_n):
        return 0.0
    return float(np.clip(avg_n / TARGET_SAMPLE_SIZE, 0.0, 1.0))


def freshness_score(rest_days_home: float | None, rest_days_away: float | None) -> float:
    values = [d for d in (rest_days_home, rest_days_away) if d is not None and not pd.isna(d)]
    if not values:
        return 0.5  # sin informacion de descanso: neutral, no penaliza ni premia
    avg_days = float(np.mean(values))
    return float(np.clip(1.0 - avg_days / MAX_STALE_DAYS, 0.0, 1.0))


def compute_data_quality(row: pd.Series, key_features: list[str]) -> float:
    completeness = completeness_score(row, key_features)
    sample_size = sample_size_score(
        row.get("home_goals_for_n_prior", np.nan), row.get("away_goals_for_n_prior", np.nan)
    )
    freshness = freshness_score(row.get("home_rest_days"), row.get("away_rest_days"))
    return float(np.mean([completeness, sample_size, freshness]))
