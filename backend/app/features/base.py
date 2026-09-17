"""Utilidades comunes de feature engineering.

REGLA DE ORO (anti-leakage): toda funcion que calcule una feature para el
partido `i` de un equipo solo puede usar filas con `date < fecha del partido i`.
Esto se garantiza aqui aplicando SIEMPRE `shift(1)` antes de cualquier
`rolling`/`expanding`, de modo que la fila del propio partido nunca entra en
su propia ventana.
"""

from __future__ import annotations

import pandas as pd

WINDOWS = (3, 5, 10)


def build_team_match_long(matches: pd.DataFrame) -> pd.DataFrame:
    """Convierte una tabla de partidos (una fila por partido) a formato largo
    (una fila por equipo y partido), para poder calcular rolling stats por
    equipo independientemente de si jugo en casa o fuera.

    `matches` debe tener, como minimo: match_id, date, home_team_id,
    away_team_id, home_goals, away_goals y las columnas de estadisticas
    opcionales (shots, corners, etc.) con prefijo home_/away_.
    """
    home = matches.rename(
        columns={
            "home_team_id": "team_id",
            "away_team_id": "opponent_id",
            "home_goals": "goals_for",
            "away_goals": "goals_against",
        }
    ).copy()
    home["is_home"] = True

    away = matches.rename(
        columns={
            "away_team_id": "team_id",
            "home_team_id": "opponent_id",
            "away_goals": "goals_for",
            "home_goals": "goals_against",
        }
    ).copy()
    away["is_home"] = False

    stat_pairs = {
        "shots": ("home_shots", "away_shots"),
        "shots_on_target": ("home_shots_on_target", "away_shots_on_target"),
        "corners": ("home_corners", "away_corners"),
        "fouls": ("home_fouls", "away_fouls"),
        "yellow_cards": ("home_yellow_cards", "away_yellow_cards"),
        "red_cards": ("home_red_cards", "away_red_cards"),
        "xg": ("home_xg", "away_xg"),
    }
    for stat, (home_col, away_col) in stat_pairs.items():
        if home_col in matches.columns and away_col in matches.columns:
            home[f"{stat}_for"] = matches[home_col]
            home[f"{stat}_against"] = matches[away_col]
            away[f"{stat}_for"] = matches[away_col]
            away[f"{stat}_against"] = matches[home_col]

    keep_cols = [
        c
        for c in home.columns
        if c not in {"home_shots", "away_shots", "home_shots_on_target", "away_shots_on_target",
                     "home_corners", "away_corners", "home_fouls", "away_fouls",
                     "home_yellow_cards", "away_yellow_cards", "home_red_cards", "away_red_cards",
                     "home_xg", "away_xg"}
    ]
    long_df = pd.concat([home[keep_cols], away[keep_cols]], ignore_index=True)
    long_df = long_df.sort_values(["team_id", "date"]).reset_index(drop=True)
    return long_df


def rolling_prior_mean(series: pd.Series, window: int | None) -> pd.Series:
    """Media movil de las N observaciones ANTERIORES (excluida la actual).

    window=None -> expanding (todo el historial previo disponible).
    """
    shifted = series.shift(1)
    if window is None:
        return shifted.expanding(min_periods=1).mean()
    return shifted.rolling(window=window, min_periods=1).mean()


def sample_size_prior(series: pd.Series, window: int | None) -> pd.Series:
    """Numero de observaciones previas realmente disponibles (para data quality)."""
    shifted = series.shift(1)
    if window is None:
        return shifted.expanding(min_periods=1).count()
    return shifted.rolling(window=window, min_periods=1).count()
