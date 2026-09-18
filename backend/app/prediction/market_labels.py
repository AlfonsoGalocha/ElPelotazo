"""Definicion unica de los mercados de goles del MVP (Fase 1).

Un solo lugar define, para cada mercado, tanto (a) como etiquetar el
resultado real de un partido finalizado (para entrenar/evaluar) como (b) como
leer la probabilidad correspondiente de una matriz de marcador conjunta
(para cualquier modelo). Evita que ambos calculos diverjan.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MarketSpec:
    display_name: str
    kind: str  # "over" | "under" | "btts" | "match_result"
    line: float | None = None


MARKET_DEFINITIONS: dict[str, MarketSpec] = {
    "over_1_5": MarketSpec("Over 1.5 Goals", "over", 1.5),
    "over_2_5": MarketSpec("Over 2.5 Goals", "over", 2.5),
    "under_2_5": MarketSpec("Under 2.5 Goals", "under", 2.5),
    "over_3_5": MarketSpec("Over 3.5 Goals", "over", 3.5),
    "btts": MarketSpec("Both Teams To Score", "btts", None),
    # 1X2: tres mercados BINARIOS independientes ("¿gana el local?", "¿empata?",
    # "¿gana el visitante?"), no uno multiclase -- encaja con la arquitectura
    # existente (un clasificador binario por mercado en ml_classifier.py, un
    # score_matrix -> probabilidad por mercado en probability.py) sin tocar
    # nada de esa infraestructura. `line` no aplica (None, igual que btts).
    "home_win": MarketSpec("Victoria Local (1)", "match_result", None),
    "draw": MarketSpec("Empate (X)", "match_result", None),
    "away_win": MarketSpec("Victoria Visitante (2)", "match_result", None),
}

# Selection en MatchOdds/RawOddsRecord (ver ingestion/odds/provider.py y
# history_dataset.py: mismo "match_result" market, selecciones
# "home"/"draw"/"away") para cada market_key de 1X2 -- unico punto que
# traduce entre el nombre interno del mercado y la selection real del
# proveedor de cuotas, para que no diverjan si se toca uno sin el otro.
MATCH_RESULT_SELECTIONS: dict[str, str] = {"home_win": "home", "draw": "draw", "away_win": "away"}


def label_for_market(table: pd.DataFrame, market_key: str) -> pd.Series:
    """Etiqueta booleana real (True = la seleccion se cumplio) para un partido finalizado."""
    spec = MARKET_DEFINITIONS[market_key]

    if spec.kind == "match_result":
        if market_key == "home_win":
            return table["home_goals"] > table["away_goals"]
        if market_key == "draw":
            return table["home_goals"] == table["away_goals"]
        if market_key == "away_win":
            return table["home_goals"] < table["away_goals"]
        raise ValueError(f"Mercado 1X2 desconocido: {market_key}")

    total_goals = table["home_goals"] + table["away_goals"]
    if spec.kind == "over":
        return total_goals > spec.line
    if spec.kind == "under":
        return total_goals < spec.line
    if spec.kind == "btts":
        return (table["home_goals"] > 0) & (table["away_goals"] > 0)
    raise ValueError(f"Tipo de mercado desconocido: {spec.kind}")


def probability_from_score_matrix(score_matrix: np.ndarray, market_key: str) -> np.ndarray:
    """`score_matrix`: shape (n_rows, K+1, K+1) con score_matrix[i, h, a] =
    P(home=h, away=a) para el partido i (h,a en 0..K, K = ultima celda = "K+").

    Devuelve un array (n_rows,) con la probabilidad del mercado pedido.
    """
    spec = MARKET_DEFINITIONS[market_key]
    n, k_plus_1, _ = score_matrix.shape

    home_grid, away_grid = np.meshgrid(np.arange(k_plus_1), np.arange(k_plus_1), indexing="ij")
    # La celda "max_k" representa "max_k o mas": para sumas de goles totales,
    # aproximamos su valor puntual como max_k (conservador para lineas .5,
    # ya que max_k es un entero exacto y las lineas del MVP son todas .5).
    total_goals_grid = home_grid + away_grid

    if spec.kind == "over":
        mask = total_goals_grid > spec.line
    elif spec.kind == "under":
        mask = total_goals_grid < spec.line
    elif spec.kind == "btts":
        mask = (home_grid > 0) & (away_grid > 0)
    elif spec.kind == "match_result":
        if market_key == "home_win":
            mask = home_grid > away_grid
        elif market_key == "draw":
            mask = home_grid == away_grid
        elif market_key == "away_win":
            mask = home_grid < away_grid
        else:
            raise ValueError(f"Mercado 1X2 desconocido: {market_key}")
    else:
        raise ValueError(f"Tipo de mercado desconocido: {spec.kind}")

    return np.array([score_matrix[i][mask].sum() for i in range(n)])
