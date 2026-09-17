"""Ratings dinamicos de fuerza ofensiva/defensiva (estilo Elo), actualizados
partido a partido en orden cronologico estricto.

Para el partido `i`, el rating usado como feature es el que existia ANTES de
jugarse `i` (se actualiza DESPUES de calcular la feature), por lo que no hay
leakage: el rating de un equipo en un instante dado solo refleja resultados
pasados.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

BASE_RATING = 1500.0
K_FACTOR = 20.0
HOME_ADVANTAGE = 60.0  # puntos Elo equivalentes a jugar en casa


@dataclass
class TeamRating:
    attack: float = BASE_RATING
    defense: float = BASE_RATING


def _expected_goal_diff(attack: float, defense: float) -> float:
    """Traduce la diferencia de rating a un goal-diff esperado (heuristica log-lineal)."""
    return (attack - defense) / 400.0


def compute_strength_features(matches: pd.DataFrame) -> pd.DataFrame:
    """Devuelve un dataframe con, por match_id: home/away attack/defense rating
    PRE-partido y home_advantage estimada, y actualiza los ratings tras cada
    resultado conocido.

    Requiere `matches` ordenado cronologicamente y con columnas match_id,
    date, home_team_id, away_team_id, home_goals, away_goals (estas dos
    ultimas pueden ser NaN para partidos futuros: simplemente no actualizan
    el rating).
    """
    ratings: dict[int, TeamRating] = {}
    rows = []

    for row in matches.sort_values("date").itertuples(index=False):
        home_id, away_id = row.home_team_id, row.away_team_id
        home_rating = ratings.setdefault(home_id, TeamRating())
        away_rating = ratings.setdefault(away_id, TeamRating())

        rows.append(
            {
                "match_id": row.match_id,
                "home_attack_strength": home_rating.attack,
                "home_defense_strength": home_rating.defense,
                "away_attack_strength": away_rating.attack,
                "away_defense_strength": away_rating.defense,
                "home_advantage": HOME_ADVANTAGE,
            }
        )

        home_goals, away_goals = getattr(row, "home_goals", None), getattr(row, "away_goals", None)
        if home_goals is None or away_goals is None or pd.isna(home_goals) or pd.isna(away_goals):
            continue

        expected_diff = _expected_goal_diff(home_rating.attack, away_rating.defense) + (
            HOME_ADVANTAGE / 400.0
        )
        actual_diff = float(home_goals - away_goals)
        error = actual_diff - expected_diff

        home_rating.attack += K_FACTOR * error
        away_rating.defense -= K_FACTOR * error

        expected_diff_away = _expected_goal_diff(away_rating.attack, home_rating.defense)
        actual_diff_away = float(away_goals - home_goals)
        error_away = actual_diff_away - expected_diff_away
        away_rating.attack += K_FACTOR * error_away
        home_rating.defense -= K_FACTOR * error_away

    return pd.DataFrame(rows)
