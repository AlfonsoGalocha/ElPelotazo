"""Tests anti-leakage (seccion 14/36 del brief).

Una prediccion creada para el partido del dia X NUNCA puede depender de
estadisticas de partidos jugados en o despues de X.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.app.features.goals import build_match_feature_table
from backend.tests.fixtures.synthetic import generate_synthetic_matches


def test_rolling_form_only_uses_strictly_prior_matches():
    matches = generate_synthetic_matches(n_teams=6, n_seasons=2, seed=1)
    table = build_match_feature_table(matches)

    team_id = 1
    team_matches = matches[
        (matches["home_team_id"] == team_id) | (matches["away_team_id"] == team_id)
    ].sort_values("date")

    # Tomamos el 6o partido de este equipo (indice 5): debe existir historial previo.
    target_match_id = team_matches.iloc[5]["match_id"]
    target_row = table[table["match_id"] == target_match_id].iloc[0]

    prior_matches = team_matches.iloc[:5]
    goals_for = []
    for _, m in prior_matches.iterrows():
        if m["home_team_id"] == team_id:
            goals_for.append(m["home_goals"])
        else:
            goals_for.append(m["away_goals"])
    # Ventana 5: promedio de exactamente los 5 partidos previos.
    expected_avg_last5 = float(np.mean(goals_for[-5:]))

    is_home = target_row["home_team_id"] == team_id
    prefix = "home" if is_home else "away"
    actual = target_row[f"{prefix}_goals_for_avg_last5"]

    assert actual == pytest.approx(expected_avg_last5)


def test_changing_a_future_match_does_not_change_past_features():
    """Si mutamos el resultado de un partido FUTURO, las features de un
    partido anterior no deben cambiar en absoluto (garantia dura anti-leakage).
    """
    matches = generate_synthetic_matches(n_teams=6, n_seasons=2, seed=2)
    table_before = build_match_feature_table(matches)

    mutated = matches.copy()
    last_match_idx = mutated.index[-1]
    mutated.loc[last_match_idx, "home_goals"] = 99
    mutated.loc[last_match_idx, "away_goals"] = 99

    table_after = build_match_feature_table(mutated)

    early_match_ids = table_before["match_id"].iloc[:20]
    feature_cols = [c for c in table_before.columns if c.endswith("_avg_last5") or c.endswith("_avg_season")]

    before_slice = table_before[table_before["match_id"].isin(early_match_ids)][feature_cols].reset_index(drop=True)
    after_slice = table_after[table_after["match_id"].isin(early_match_ids)][feature_cols].reset_index(drop=True)

    pd.testing.assert_frame_equal(before_slice, after_slice)


def test_first_match_of_a_team_has_no_prior_form():
    """El primer partido de un equipo en el dataset no puede tener una media
    de forma basada en partidos futuros: debe quedar NaN (sin datos previos)."""
    matches = generate_synthetic_matches(n_teams=6, n_seasons=1, seed=3)
    table = build_match_feature_table(matches)

    team_id = 1
    team_matches = matches[
        (matches["home_team_id"] == team_id) | (matches["away_team_id"] == team_id)
    ].sort_values("date")
    first_match_id = team_matches.iloc[0]["match_id"]
    first_row = table[table["match_id"] == first_match_id].iloc[0]

    is_home = first_row["home_team_id"] == team_id
    prefix = "home" if is_home else "away"
    assert pd.isna(first_row[f"{prefix}_goals_for_avg_last5"])
    assert first_row[f"{prefix}_goals_for_n_prior"] == 0
