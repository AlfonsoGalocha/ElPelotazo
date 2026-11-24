"""Mercado de corners (Fase 2/3 del roadmap, ahora implementado).

Enfoque deliberadamente simple y honesto: un unico Poisson sobre el TOTAL
de corners del partido (home+away), regresado sobre las mismas features
de forma que ya calcula `features/form.py` (corners_for/against — con
shift(1), sin leakage).

No se modela home/away por separado (a diferencia de goles) porque el
brief pide sobre todo lineas de TOTAL de partido para este mercado
(seccion 18/19: "Over 8.5 corners", etc.), y un unico Poisson total es mas
simple, mas robusto con el volumen de datos disponible, y evita inventar
una descomposicion home/away sin base solida.

Tarjetas (cards) se quito del todo (pedido real del usuario): ninguna
casa de las que agrega API-Football ofrece mercado de tarjetas para
estas 5 ligas (confirmado contra la respuesta real, `debug_match_odds.py`
-- de ~120 mercados distintos para un partido, ninguno menciona "card"),
asi que una prediccion de tarjetas SIEMPRE saldria "sin mercado", sin
cuota real con la que compararse -- ruido puro, nunca una señal real.

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

MAX_COUNT = 12  # cola "12+" para corners (rango realista de partido)


@dataclass(frozen=True)
class SecondaryMarketSpec:
    display_name: str
    stat_family: str  # "corners" (unica familia soportada -- ver docstring del modulo)
    kind: str  # "over" | "under"
    line: float


SECONDARY_MARKET_DEFINITIONS: dict[str, SecondaryMarketSpec] = {
    "corners_over_8_5": SecondaryMarketSpec("Over 8.5 Corners", "corners", "over", 8.5),
    "corners_over_9_5": SecondaryMarketSpec("Over 9.5 Corners", "corners", "over", 9.5),
    "corners_over_10_5": SecondaryMarketSpec("Over 10.5 Corners", "corners", "over", 10.5),
    "corners_under_9_5": SecondaryMarketSpec("Under 9.5 Corners", "corners", "under", 9.5),
}

STAT_FAMILY_TOTAL_COLUMNS = {
    "corners": ("home_corners", "away_corners"),
}


def compute_total_column(table: pd.DataFrame, stat_family: str) -> pd.Series:
    """Total real de corners de un partido, a partir de las columnas
    crudas (solo para ETIQUETAR el entrenamiento; nunca se usa como
    feature). NO se rellenan nulos con 0: un partido sin corners
    registrados (fuente que no trae esa estadistica para ese partido en
    concreto) debe dar NaN, no "0 corners reales" -- ver bug real
    reportado por el usuario en `has_real_count_data`."""
    if stat_family == "corners":
        cols = STAT_FAMILY_TOTAL_COLUMNS["corners"]
        home, away = table[cols[0]], table[cols[1]]
        total = (home + away).where(home.ge(0) & away.ge(0))
        if "observed_total_corners" in table:
            total = table["observed_total_corners"].combine_first(total)
        return total
    raise ValueError(f"Familia de estadistica desconocida: {stat_family}")


MIN_MATCHES_FOR_SECONDARY_MARKET = 20  # minimo de partidos con dato REAL para entrenar con sentido


class InsufficientSecondaryMarketDataError(ValueError):
    """La fuente de datos no trae esta estadistica para suficientes
    partidos todavia (una competicion nueva, o sin fuente con corners
    para ella) -- nunca se entrena con una muestra vacia o demasiado
    pequenha para significar algo; ver `has_real_count_data`."""


def has_real_count_data(table: pd.DataFrame, stat_family: str) -> pd.Series:
    """Bug real reportado por el usuario (casi todos los partidos salian
    con 85%+ en "menos de 9.5 corners", sin sentido): `compute_total_column`
    hacia antes `.fillna(0)` sobre home_corners/away_corners -- un partido
    de una fuente que NUNCA trajo corners (openfootball, por ejemplo, solo
    trae resultado/calendario) se contaba como "0 corners reales" en el
    entrenamiento del Poisson, sesgando la media global hacia abajo para
    TODOS los partidos (no solo los que faltaban), y lo mismo liquidaba
    predicciones como "se cumplio menos de 9.5" para partidos cuyo
    resultado real de corners nunca se supo. Se usa este filtro ANTES de
    entrenar, evaluar o liquidar: un partido sin datos reales de corners
    se excluye del todo, nunca se trata como si tuviera 0."""
    return compute_total_column(table, stat_family).notna()


def label_for_secondary_market(table: pd.DataFrame, market_key: str) -> pd.Series:
    """Devuelve NaN (no False) en las filas sin datos reales -- ver
    `has_real_count_data`. El llamante debe filtrar esas filas antes de
    usar el resultado como una etiqueta booleana limpia (nunca liquidar o
    evaluar con una etiqueta inventada)."""
    spec = SECONDARY_MARKET_DEFINITIONS[market_key]
    total = compute_total_column(table, spec.stat_family)
    label = total > spec.line if spec.kind == "over" else total < spec.line
    return label.where(has_real_count_data(table, spec.stat_family))


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
        # Ver docstring de `has_real_count_data`: un partido sin esta
        # estadistica registrada se excluye del entrenamiento, nunca se
        # trata como si tuviera 0.
        finished = finished[has_real_count_data(finished, self.stat_family)]
        if len(finished) < MIN_MATCHES_FOR_SECONDARY_MARKET:
            raise InsufficientSecondaryMarketDataError(
                f"Solo {len(finished)} partidos con dato real de '{self.stat_family}' "
                f"(minimo {MIN_MATCHES_FOR_SECONDARY_MARKET}): no se entrena este mercado "
                "para no ajustar un Poisson sobre una muestra sin sentido estadistico."
            )
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
            p_over = poisson.sf(np.floor(spec.line), lam)
            p = p_over if spec.kind == "over" else 1.0 - p_over
            out[market_key] = np.clip(p, 1e-3, 1.0 - 1e-3)
        return out
