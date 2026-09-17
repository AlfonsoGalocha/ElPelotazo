"""Walk-forward splits temporales (expanding window).

Prohibido cualquier split aleatorio en series temporales (seccion 13/55 del
brief). Cada fold entrena con TODO el pasado disponible y valida en la
temporada siguiente, nunca al reves.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class WalkForwardFold:
    train_seasons: list[str]
    test_season: str
    train_index: pd.Index
    test_index: pd.Index


def expanding_window_splits(
    table: pd.DataFrame, season_col: str = "season_label", min_train_seasons: int = 2
) -> list[WalkForwardFold]:
    """`table` debe tener una columna `season_col` y estar ordenada por fecha.

    La primera temporada de test es la (min_train_seasons + 1)-esima
    temporada disponible, para no evaluar con un entrenamiento ridiculamente
    pequenho (seccion 57: cold start).
    """
    seasons_in_order = list(dict.fromkeys(table.sort_values("date")[season_col]))
    folds: list[WalkForwardFold] = []

    for i in range(min_train_seasons, len(seasons_in_order)):
        train_seasons = seasons_in_order[:i]
        test_season = seasons_in_order[i]
        train_index = table.index[table[season_col].isin(train_seasons)]
        test_index = table.index[table[season_col] == test_season]
        folds.append(WalkForwardFold(train_seasons, test_season, train_index, test_index))
    return folds


def rolling_window_splits(
    table: pd.DataFrame, season_col: str = "season_label", window_size: int = 3
) -> list[WalkForwardFold]:
    """Variante de ventana deslizante de tamanho fijo (en vez de expanding)."""
    seasons_in_order = list(dict.fromkeys(table.sort_values("date")[season_col]))
    folds: list[WalkForwardFold] = []

    for i in range(window_size, len(seasons_in_order)):
        train_seasons = seasons_in_order[i - window_size : i]
        test_season = seasons_in_order[i]
        train_index = table.index[table[season_col].isin(train_seasons)]
        test_index = table.index[table[season_col] == test_season]
        folds.append(WalkForwardFold(train_seasons, test_season, train_index, test_index))
    return folds
