from __future__ import annotations

import numpy as np
import pytest

from backend.app.features.goals import build_match_feature_table
from backend.app.prediction.secondary_markets import (
    SECONDARY_MARKET_DEFINITIONS,
    TotalCountPoissonModel,
    compute_total_column,
    label_for_secondary_market,
)
from backend.tests.fixtures.synthetic import generate_synthetic_matches


@pytest.fixture(scope="module")
def synthetic_table():
    matches = generate_synthetic_matches(n_teams=10, n_seasons=4, seed=13)
    return build_match_feature_table(matches)


def test_compute_total_column_cards_matches_manual_sum(synthetic_table):
    row = synthetic_table.iloc[0]
    expected = (
        row["home_yellow_cards"] + row["away_yellow_cards"]
        + 2 * row["home_red_cards"] + 2 * row["away_red_cards"]
    )
    actual = compute_total_column(synthetic_table.iloc[[0]], "cards").iloc[0]
    assert actual == pytest.approx(expected)


def test_compute_total_column_corners_matches_manual_sum(synthetic_table):
    row = synthetic_table.iloc[0]
    expected = row["home_corners"] + row["away_corners"]
    actual = compute_total_column(synthetic_table.iloc[[0]], "corners").iloc[0]
    assert actual == pytest.approx(expected)


@pytest.mark.parametrize("stat_family", ["cards", "corners"])
def test_total_count_model_probabilities_in_unit_interval(synthetic_table, stat_family):
    train = synthetic_table[synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]]
    model = TotalCountPoissonModel(stat_family).fit(train)
    probs = model.predict_market_probabilities(train.head(20))
    for market_key, values in probs.items():
        assert (values > 0).all() and (values < 1).all(), market_key


def test_over_lines_are_monotonic_for_cards(synthetic_table):
    """P(Over 3.5) >= P(Over 4.5) >= P(Over 5.5): evento mas laxo primero."""
    train = synthetic_table[synthetic_table["season_label"] != synthetic_table["season_label"].iloc[-1]]
    model = TotalCountPoissonModel("cards").fit(train)
    probs = model.predict_market_probabilities(train.head(20))
    assert (probs["cards_over_3_5"] >= probs["cards_over_4_5"] - 1e-9).all()
    assert (probs["cards_over_4_5"] >= probs["cards_over_5_5"] - 1e-9).all()


def test_label_for_secondary_market_matches_real_outcome(synthetic_table):
    finished = synthetic_table.dropna(subset=["home_goals", "away_goals"])
    labels = label_for_secondary_market(finished, "corners_over_9_5")
    totals = compute_total_column(finished, "corners")
    np.testing.assert_array_equal(labels.to_numpy(), (totals > 9.5).to_numpy())


def test_all_secondary_markets_have_a_stat_family():
    for spec in SECONDARY_MARKET_DEFINITIONS.values():
        assert spec.stat_family in {"cards", "corners"}
