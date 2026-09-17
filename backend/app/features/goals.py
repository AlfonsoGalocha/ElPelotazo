"""Ensambla la tabla de features final usada por los modelos de goles.

Pipeline: matches (ancho) -> team_match_long -> form/home_away/rest ->
strength (sobre matches) -> merge de vuelta a ancho (home_*/away_*) ->
columnas de diferencia.

Este es el UNICO punto de entrada que deben usar entrenamiento y prediccion,
para garantizar que ambos ven exactamente las mismas features calculadas de
la misma forma (evita skew train/serving).
"""

from __future__ import annotations

import pandas as pd

from backend.app.features.base import build_team_match_long
from backend.app.features.form import compute_form_features
from backend.app.features.home_away import compute_home_away_split_features
from backend.app.features.rest import compute_rest_days
from backend.app.features.strength import compute_strength_features

# Ventana usada como feature "principal" de forma reciente (equilibrio entre
# reactividad y tamanho de muestra). Las demas ventanas quedan calculadas y
# disponibles pero no todas se usan por defecto en el modelo baseline.
PRIMARY_WINDOW = 5
HOME_AWAY_WINDOW = 10

DIFFERENCE_STATS = ["goals_for_avg_last5", "goals_against_avg_last5",
                     "shots_for_avg_last5", "xg_for_avg_last5"]


def build_match_feature_table(matches: pd.DataFrame) -> pd.DataFrame:
    """`matches`: una fila por partido (match_id, date, home_team_id,
    away_team_id, home_goals, away_goals, columnas home_*/away_* de stats).

    Devuelve una fila por partido con features home_/away_ + diferencias,
    lista para alimentar cualquier modelo (Poisson, Dixon-Coles, ML).
    """
    matches = matches.copy()
    matches["date"] = pd.to_datetime(matches["date"])

    team_long = build_team_match_long(matches)
    team_long = compute_form_features(team_long)
    team_long = compute_home_away_split_features(team_long, window=HOME_AWAY_WINDOW)
    team_long = compute_rest_days(team_long)

    strength = compute_strength_features(
        matches[["match_id", "date", "home_team_id", "away_team_id", "home_goals", "away_goals"]]
    )

    feature_cols = [c for c in team_long.columns if c not in {
        "match_id", "team_id", "opponent_id", "date", "is_home",
        "competition_id", "season_id", "matchday", "referee_id",
        "goals_for", "goals_against",
    } and not c.endswith("_n_prior")]
    # nos quedamos tambien con *_n_prior para calcular data quality despues
    n_prior_cols = [c for c in team_long.columns if c.endswith("_n_prior")]

    home_view = team_long[team_long["is_home"]][["match_id", *feature_cols, *n_prior_cols]]
    home_view = home_view.rename(columns={c: f"home_{c}" for c in [*feature_cols, *n_prior_cols]})

    away_view = team_long[~team_long["is_home"]][["match_id", *feature_cols, *n_prior_cols]]
    away_view = away_view.rename(columns={c: f"away_{c}" for c in [*feature_cols, *n_prior_cols]})

    table = matches.merge(home_view, on="match_id", how="left")
    table = table.merge(away_view, on="match_id", how="left")
    table = table.merge(strength, on="match_id", how="left")

    for stat in DIFFERENCE_STATS:
        home_col, away_col = f"home_{stat}", f"away_{stat}"
        if home_col in table.columns and away_col in table.columns:
            table[f"{stat}_difference"] = table[home_col] - table[away_col]

    table["attack_strength_difference"] = table["home_attack_strength"] - table["away_defense_strength"]
    table["defense_strength_difference"] = table["away_attack_strength"] - table["home_defense_strength"]

    return table


def feature_columns(table: pd.DataFrame) -> list[str]:
    """Lista de columnas numericas de features (excluye ids/goles/metadatos)."""
    excluded = {
        "match_id", "provider", "provider_id", "competition_id", "season_id",
        "matchday", "date", "home_team_id", "away_team_id", "referee_id",
        "home_goals", "away_goals", "home_goals_ht", "away_goals_ht", "status",
    }
    return [c for c in table.columns if c not in excluded and table[c].dtype.kind in "fi"]
