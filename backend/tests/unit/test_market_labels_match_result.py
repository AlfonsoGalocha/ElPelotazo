from __future__ import annotations

import numpy as np
import pandas as pd

from backend.app.prediction.market_labels import (
    MARKET_DEFINITIONS,
    MATCH_RESULT_SELECTIONS,
    label_for_market,
    probability_from_score_matrix,
)


def test_match_result_markets_are_registered():
    assert {"home_win", "draw", "away_win"} <= set(MARKET_DEFINITIONS)
    for key in ("home_win", "draw", "away_win"):
        assert MARKET_DEFINITIONS[key].kind == "match_result"
        assert MARKET_DEFINITIONS[key].line is None


def test_match_result_selections_map_to_odds_provider_values():
    """`RawOddsRecord`/`MatchOdds` para 1X2 usan selection "home"/"draw"/
    "away" (ver ingestion/odds/provider.py::_parse_event, mercado h2h) --
    este mapping es el UNICO punto que traduce el market_key interno a esa
    selection real, para que ambos lados no puedan divergir."""
    assert MATCH_RESULT_SELECTIONS == {"home_win": "home", "draw": "draw", "away_win": "away"}


def test_label_for_market_home_win_draw_away_win_are_mutually_exclusive_and_exhaustive():
    table = pd.DataFrame(
        {
            "home_goals": [2, 1, 0, 3],
            "away_goals": [1, 1, 2, 3],
        }
    )
    home_win = label_for_market(table, "home_win")
    draw = label_for_market(table, "draw")
    away_win = label_for_market(table, "away_win")

    # Exactamente una de las tres se cumple para cada partido (1X2 es
    # exhaustivo y mutuamente excluyente por definicion).
    totals = home_win.astype(int) + draw.astype(int) + away_win.astype(int)
    assert (totals == 1).all()

    assert list(home_win) == [True, False, False, False]
    assert list(draw) == [False, True, False, True]
    assert list(away_win) == [False, False, True, False]


def test_probability_from_score_matrix_match_result_sums_to_one():
    """Las probabilidades de home_win + draw + away_win derivadas del MISMO
    score_matrix deben sumar (aprox) 1.0: son las 3 unicas particiones
    posibles del espacio de resultados, sin solapamiento ni hueco."""
    k = 6
    rng = np.random.default_rng(42)
    raw = rng.random((2, k + 1, k + 1))
    score_matrix = raw / raw.sum(axis=(1, 2), keepdims=True)

    p_home = probability_from_score_matrix(score_matrix, "home_win")
    p_draw = probability_from_score_matrix(score_matrix, "draw")
    p_away = probability_from_score_matrix(score_matrix, "away_win")

    np.testing.assert_allclose(p_home + p_draw + p_away, 1.0, atol=1e-9)
    assert (p_home > 0).all() and (p_draw > 0).all() and (p_away > 0).all()
