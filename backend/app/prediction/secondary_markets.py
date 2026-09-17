"""Mercados de tarjetas y corners (Fase 2/3 del roadmap, ahora implementados).

Enfoque deliberadamente simple y honesto: un unico Poisson sobre el TOTAL
de tarjetas/corners del partido (home+away), regresado sobre las mismas
features de forma que ya calcula `features/form.py` (yellow_cards_for/against,
corners_for/against, fouls_for/against — todas con shift(1), sin leakage).

No se modela home/away por separado (a diferencia de goles) porque el
brief pide sobre todo lineas de TOTAL de partido para estos mercados
(seccion 18/19: "Over 3.5 cards", "Over 8.5 corners", etc.), y un unico
Poisson total es mas simple, mas robusto con el volumen de datos disponible,
y evita inventar una descomposicion home/away sin base solida.

Arbitro NO se usa todavia como feature (features/referee.py esta listo pero
el dataset ingerido no trae fiablemente `referee_raw` desde todas las
fuentes); documentado como mejora futura, no oculto.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from backend.app.features.goals import feature_columns

MAX_COUNT = 12  # cola "12+" para tarjetas/corners (rango realista de partido)


@dataclass(frozen=True)
class SecondaryMarketSpec:
    display_name: str
    stat_family: str  # "cards" | "corners"
    kind: str  # "over" | "under"
    line: float


SECONDARY_MARKET_DEFINITIONS: dict[str, SecondaryMarketSpec] = {
    "cards_over_3_5": SecondaryMarketSpec("Over 3.5 Cards", "cards", "over", 3.5),
    "cards_over_4_5": SecondaryMarketSpec("Over 4.5 Cards", "cards", "over", 4.5),
    "cards_over_5_5": SecondaryMarketSpec("Over 5.5 Cards", "cards", "over", 5.5),
    "cards_under_4_5": SecondaryMarketSpec("Under 4.5 Cards", "cards", "under", 4.5),
    "corners_over_8_5": SecondaryMarketSpec("Over 8.5 Corners", "corners", "over", 8.5),
    "corners_over_9_5": SecondaryMarketSpec("Over 9.5 Corners", "corners", "over", 9.5),
    "corners_over_10_5": SecondaryMarketSpec("Over 10.5 Corners", "corners", "over", 10.5),
    "corners_under_9_5": SecondaryMarketSpec("Under 9.5 Corners", "corners", "under", 9.5),
}

STAT_FAMILY_TOTAL_COLUMNS = {
    "cards": ("home_yellow_cards", "away_yellow_cards", "home_red_cards", "away_red_cards"),
    "corners": ("home_corners", "away_corners"),
}


def compute_total_column(table: pd.DataFrame, stat_family: str) -> pd.Series:
    """Total real de tarjetas (amarillas + 2x rojas, ponderacion estandar de
    disciplina) o corners de un partido, a partir de las columnas crudas
    (solo para ETIQUETAR el entrenamiento; nunca se usa como feature)."""
    if stat_family == "cards":
        cols = STAT_FAMILY_TOTAL_COLUMNS["cards"]
        return (
            table[cols[0]].fillna(0)
            + table[cols[1]].fillna(0)
            + table[cols[2]].fillna(0)
            + table[cols[3]].fillna(0)
        )
    if stat_family == "corners":
        cols = STAT_FAMILY_TOTAL_COLUMNS["corners"]
        return table[cols[0]].fillna(0) + table[cols[1]].fillna(0)
    raise ValueError(f"Familia de estadistica desconocida: {stat_family}")


def label_for_secondary_market(table: pd.DataFrame, market_key: str) -> pd.Series:
    spec = SECONDARY_MARKET_DEFINITIONS[market_key]
    total = compute_total_column(table, spec.stat_family)
    if spec.kind == "over":
        return total > spec.line
    return total < spec.line


class TotalCountPoissonModel:
    """Poisson regression sobre el TOTAL (home+away) de una estadistica de
    conteo (tarjetas o corners), reutilizando feature_columns() (que ya
    excluye las estadisticas crudas del propio partido, ver features/goals.py)."""

    def __init__(self, stat_family: str, alpha: float = 1.0) -> None:
        self.stat_family = stat_family
        self.alpha = alpha
        self.feature_cols_: list[str] = []
        self.pipeline_ = None

    def fit(self, table: pd.DataFrame) -> TotalCountPoissonModel:
        finished = table.dropna(subset=["home_goals", "away_goals"])
        self.feature_cols_ = feature_columns(finished)
        target = compute_total_column(finished, self.stat_family)

        self.pipeline_ = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            PoissonRegressor(alpha=self.alpha, max_iter=500),
        ).fit(finished[self.feature_cols_], target)
        return self

    def predict_lambda(self, table: pd.DataFrame) -> np.ndarray:
        return self.pipeline_.predict(table[self.feature_cols_])

    def predict_market_probabilities(self, table: pd.DataFrame) -> dict[str, np.ndarray]:
        lam = np.clip(self.predict_lambda(table), 1e-6, None)
        out: dict[str, np.ndarray] = {}
        for market_key, spec in SECONDARY_MARKET_DEFINITIONS.items():
            if spec.stat_family != self.stat_family:
                continue
            # P(total > line) = 1 - CDF(floor(line)) para lineas .5
            p_over = 1.0 - poisson.cdf(np.floor(spec.line), lam)
            p = p_over if spec.kind == "over" else 1.0 - p_over
            out[market_key] = np.clip(p, 1e-3, 1.0 - 1e-3)
        return out
