from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.app.features.goals import build_match_feature_table
from backend.app.prediction.secondary_markets import (
    MIN_MATCHES_FOR_SECONDARY_MARKET,
    SECONDARY_MARKET_DEFINITIONS,
    InsufficientSecondaryMarketDataError,
    TotalCountPoissonModel,
    compute_total_column,
    has_real_count_data,
    label_for_secondary_market,
)
from backend.tests.fixtures.synthetic import generate_synthetic_matches


@pytest.fixture(scope="module")
def synthetic_table():
    matches = generate_synthetic_matches(n_teams=10, n_seasons=4, seed=13)
    return build_match_feature_table(matches)


def test_compute_total_column_rejects_cards():
    """Pedido real del usuario: tarjetas se quito del todo (ninguna casa
    agregada por API-Football ofrece ese mercado para estas ligas, ver
    docstring del modulo) -- "cards" ya no es una familia soportada."""
    with pytest.raises(ValueError, match="cards"):
        compute_total_column(pd.DataFrame({"home_corners": [0], "away_corners": [0]}), "cards")


def test_compute_total_column_corners_matches_manual_sum(synthetic_table):
    row = synthetic_table.iloc[0]
    expected = row["home_corners"] + row["away_corners"]
    actual = compute_total_column(synthetic_table.iloc[[0]], "corners").iloc[0]
    assert actual == pytest.approx(expected)


def test_compute_total_column_does_not_fill_missing_corners_with_zero():
    """Bug real reportado por el usuario (casi todos los partidos salian
    con 85%+ en "menos de 9.5 corners", sin sentido): un partido sin
    corners reales (fuente que no los trae, p.ej. openfootball) daba
    antes `.fillna(0)` -> "0 corners reales", sesgando el entrenamiento.
    Debe dar NaN, nunca 0, para que se pueda excluir mas adelante."""
    table = pd.DataFrame({"home_corners": [5.0, np.nan, 3.0], "away_corners": [4.0, 2.0, np.nan]})
    total = compute_total_column(table, "corners")
    assert total.tolist()[0] == pytest.approx(9.0)
    assert total.isna().tolist() == [False, True, True]


def test_has_real_count_data_flags_rows_with_any_missing_column():
    table = pd.DataFrame({"home_corners": [5.0, np.nan, 3.0], "away_corners": [4.0, 2.0, np.nan]})
    assert has_real_count_data(table, "corners").tolist() == [True, False, False]


def test_fit_raises_when_too_few_real_matches():
    """Competicion nueva (o sin fuente de corners todavia): nunca se
    entrena un Poisson sobre una muestra vacia o casi vacia."""
    table = pd.DataFrame(
        {
            "home_goals": [1.0, 2.0],
            "away_goals": [0.0, 1.0],
            "home_corners": [5.0, np.nan],
            "away_corners": [4.0, np.nan],
        }
    )
    with pytest.raises(
        InsufficientSecondaryMarketDataError,
        match=f"Solo 1 partidos.*minimo {MIN_MATCHES_FOR_SECONDARY_MARKET}",
    ):
        TotalCountPoissonModel("corners").fit(table)


def test_fit_ignores_matches_without_real_corners_data(synthetic_table):
    """Mismo bug que arriba, pero demostrado end-to-end sobre el propio
    Poisson: anhadir un monton de partidos SIN corners reales al
    entrenamiento no debe cambiar absolutamente nada la prediccion,
    porque esas filas se excluyen -- antes (con `.fillna(0)`) si
    cambiaba, y mucho, porque esas filas competian como "0 corners"."""
    train = synthetic_table[synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]]
    clean_model = TotalCountPoissonModel("corners").fit(train)
    clean_lambda = clean_model.predict_lambda(train.head(5))

    fake_missing = train.head(len(train) // 2).copy()
    fake_missing["home_corners"] = np.nan
    fake_missing["away_corners"] = np.nan
    contaminated = pd.concat([train, fake_missing], ignore_index=True)

    contaminated_model = TotalCountPoissonModel("corners").fit(contaminated)
    contaminated_lambda = contaminated_model.predict_lambda(train.head(5))

    np.testing.assert_allclose(clean_lambda, contaminated_lambda, rtol=1e-9)


def test_label_for_secondary_market_is_nan_when_data_missing():
    table = pd.DataFrame({"home_corners": [10.0, np.nan], "away_corners": [2.0, 3.0]})
    labels = label_for_secondary_market(table, "corners_over_9_5")
    assert bool(labels.iloc[0]) is True
    assert pd.isna(labels.iloc[1])


@pytest.mark.parametrize("stat_family", ["corners"])
def test_total_count_model_probabilities_in_unit_interval(synthetic_table, stat_family):
    train = synthetic_table[synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]]
    model = TotalCountPoissonModel(stat_family).fit(train)
    probs = model.predict_market_probabilities(train.head(20))
    for market_key, values in probs.items():
        assert (values > 0).all() and (values < 1).all(), market_key


def test_over_lines_are_monotonic_for_corners(synthetic_table):
    """P(Over 8.5) >= P(Over 9.5) >= P(Over 10.5): evento mas laxo primero."""
    train = synthetic_table[synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]]
    model = TotalCountPoissonModel("corners").fit(train)
    probs = model.predict_market_probabilities(train.head(20))
    assert (probs["corners_over_8_5"] >= probs["corners_over_9_5"] - 1e-9).all()
    assert (probs["corners_over_9_5"] >= probs["corners_over_10_5"] - 1e-9).all()


def test_label_for_secondary_market_matches_real_outcome(synthetic_table):
    finished = synthetic_table.dropna(subset=["home_goals", "away_goals"])
    labels = label_for_secondary_market(finished, "corners_over_9_5")
    totals = compute_total_column(finished, "corners")
    np.testing.assert_array_equal(labels.to_numpy(), (totals > 9.5).to_numpy())


def test_all_secondary_markets_have_a_stat_family():
    for spec in SECONDARY_MARKET_DEFINITIONS.values():
        assert spec.stat_family == "corners"
