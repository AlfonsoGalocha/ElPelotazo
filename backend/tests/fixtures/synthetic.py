"""Generador de partidos SINTETICOS para tests y smoke-tests.

IMPORTANTE: esto es exclusivamente para pruebas automatizadas. Nunca se usa
para poblar data/ ni para simular que existen datos reales (seccion 70 del
brief: "no inventes datos"). El entorno de desarrollo de este proyecto tiene
egress de red restringido a un allowlist que NO incluye football-data.co.uk,
asi que la ingesta real solo puede ejecutarse en un entorno con acceso a
internet sin restringir (ver docs/data_sources.md).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd


def generate_synthetic_matches(
    n_teams: int = 10, n_seasons: int = 4, seed: int = 42
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    team_ids = list(range(1, n_teams + 1))
    true_attack = {t: rng.normal(1.3, 0.3) for t in team_ids}
    true_defense = {t: rng.normal(1.1, 0.25) for t in team_ids}

    rows = []
    match_id = 1
    start_year = 2020
    for season_idx in range(n_seasons):
        season_label = f"{start_year + season_idx}/{str(start_year + season_idx + 1)[-2:]}"
        season_start = dt.date(start_year + season_idx, 8, 15)
        matchday = 0
        for home in team_ids:
            for away in team_ids:
                if home == away:
                    continue
                matchday += 1
                date = season_start + dt.timedelta(days=matchday * 3)
                home_lambda = max(0.2, true_attack[home] - true_defense[away] * 0.5 + 0.3)
                away_lambda = max(0.2, true_attack[away] - true_defense[home] * 0.5)
                home_goals = rng.poisson(home_lambda)
                away_goals = rng.poisson(away_lambda)

                rows.append(
                    {
                        "match_id": match_id,
                        "competition_id": 1,
                        "season_id": season_idx + 1,
                        "season_label": season_label,
                        "date": pd.Timestamp(date),
                        "home_team_id": home,
                        "away_team_id": away,
                        "referee_id": None,
                        "home_goals": home_goals,
                        "away_goals": away_goals,
                        "home_shots": int(rng.poisson(12)),
                        "away_shots": int(rng.poisson(10)),
                        "home_shots_on_target": int(rng.poisson(5)),
                        "away_shots_on_target": int(rng.poisson(4)),
                        "home_corners": int(rng.poisson(5)),
                        "away_corners": int(rng.poisson(4)),
                        "home_fouls": int(rng.poisson(11)),
                        "away_fouls": int(rng.poisson(11)),
                        "home_yellow_cards": int(rng.poisson(2)),
                        "away_yellow_cards": int(rng.poisson(2)),
                        "home_red_cards": int(rng.binomial(1, 0.05)),
                        "away_red_cards": int(rng.binomial(1, 0.05)),
                        "home_xg": float(np.clip(rng.normal(home_lambda, 0.3), 0, None)),
                        "away_xg": float(np.clip(rng.normal(away_lambda, 0.3), 0, None)),
                    }
                )
                match_id += 1
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
