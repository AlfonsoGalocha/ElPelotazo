from __future__ import annotations

import numpy as np
import pandas as pd

from backend.app.prediction.market_labels import (
    DOUBLE_CHANCE_COMPONENTS,
    MARKET_DEFINITIONS,
    label_for_market,
    probability_from_score_matrix,
)


def test_double_chance_and_team_goals_markets_are_registered():
    for key in ("double_chance_1x", "double_chance_x2", "double_chance_12"):
        assert MARKET_DEFINITIONS[key].kind == "double_chance"
    for key in ("home_team_over_0_5", "home_team_over_1_5", "away_team_over_0_5", "away_team_over_1_5"):
        assert MARKET_DEFINITIONS[key].kind == "team_over"


def test_double_chance_components_reference_valid_1x2_markets():
    for component_a, component_b in DOUBLE_CHANCE_COMPONENTS.values():
        assert MARKET_DEFINITIONS[component_a].kind == "match_result"
        assert MARKET_DEFINITIONS[component_b].kind == "match_result"


def test_label_for_double_chance_is_union_of_its_components():
    table = pd.DataFrame({"home_goals": [2, 1, 0, 3], "away_goals": [1, 1, 2, 3]})
    # Resultados: home_win, draw, away_win, draw
    dc_1x = label_for_market(table, "double_chance_1x")  # home_win OR draw
    dc_x2 = label_for_market(table, "double_chance_x2")  # draw OR away_win
    dc_12 = label_for_market(table, "double_chance_12")  # home_win OR away_win

    assert list(dc_1x) == [True, True, False, True]
    assert list(dc_x2) == [False, True, True, True]
    assert list(dc_12) == [True, False, True, False]


def test_label_for_team_over_uses_only_that_teams_goals():
    table = pd.DataFrame({"home_goals": [2, 0, 1], "away_goals": [0, 3, 1]})
    assert list(label_for_market(table, "home_team_over_0_5")) == [True, False, True]
    assert list(label_for_market(table, "home_team_over_1_5")) == [True, False, False]
    assert list(label_for_market(table, "away_team_over_0_5")) == [False, True, True]
    assert list(label_for_market(table, "away_team_over_1_5")) == [False, True, False]


def test_probability_double_chance_equals_sum_of_component_probabilities():
    """Los 3 resultados de 1X2 son mutuamente excluyentes: P(1 o X) debe
    ser exactamente P(1) + P(X) sobre el MISMO score_matrix, sin doble
    conteo ni hueco."""
    k = 6
    rng = np.random.default_rng(7)
    raw = rng.random((3, k + 1, k + 1))
    score_matrix = raw / raw.sum(axis=(1, 2), keepdims=True)

    p_home = probability_from_score_matrix(score_matrix, "home_win")
    p_draw = probability_from_score_matrix(score_matrix, "draw")
    p_away = probability_from_score_matrix(score_matrix, "away_win")

    p_1x = probability_from_score_matrix(score_matrix, "double_chance_1x")
    p_x2 = probability_from_score_matrix(score_matrix, "double_chance_x2")
    p_12 = probability_from_score_matrix(score_matrix, "double_chance_12")

    np.testing.assert_allclose(p_1x, p_home + p_draw, atol=1e-9)
    np.testing.assert_allclose(p_x2, p_draw + p_away, atol=1e-9)
    np.testing.assert_allclose(p_12, p_home + p_away, atol=1e-9)


def test_probability_team_over_only_depends_on_that_teams_axis():
    """P(local marca) no debe depender de cuantos goles meta el visitante:
    se calcula sumando sobre TODA la fila/columna del equipo contrario."""
    k = 4
    # Matriz donde el local SIEMPRE mete exactamente 1 gol (fila 1 = 1.0,
    # resto 0), independientemente del visitante.
    score_matrix = np.zeros((1, k + 1, k + 1))
    score_matrix[0, 1, :] = 1.0 / (k + 1)  # home=1 para cualquier away

    p_home_over_0_5 = probability_from_score_matrix(score_matrix, "home_team_over_0_5")
    p_home_over_1_5 = probability_from_score_matrix(score_matrix, "home_team_over_1_5")

    np.testing.assert_allclose(p_home_over_0_5, [1.0])  # el local siempre mete >=1
    np.testing.assert_allclose(p_home_over_1_5, [0.0])  # nunca mete >=2
