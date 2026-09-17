from __future__ import annotations

import pytest

from backend.app.market.implied_probability import implied_probability
from backend.app.market.vig import no_vig_probabilities, overround
from backend.app.prediction.edge import compute_edge, compute_expected_value
from backend.app.prediction.fair_odds import fair_odds


def test_implied_probability_basic():
    assert implied_probability(2.0) == pytest.approx(0.5)
    assert implied_probability(1.5) == pytest.approx(2 / 3)


def test_implied_probability_rejects_invalid_odds():
    with pytest.raises(ValueError):
        implied_probability(1.0)
    with pytest.raises(ValueError):
        implied_probability(0.5)


def test_overround_is_positive_for_realistic_odds():
    # Cuotas tipicas de un mercado over/under con margen del libro
    odds = {"over": 1.90, "under": 1.95}
    assert overround(odds) > 0


def test_no_vig_probabilities_sum_to_one():
    odds = {"over": 1.90, "under": 1.95}
    no_vig = no_vig_probabilities(odds)
    assert sum(no_vig.values()) == pytest.approx(1.0)
    # Sin vig, la seleccion con cuota mas baja (over, 1.90) debe tener mayor
    # probabilidad que la de cuota mas alta (under, 1.95).
    assert no_vig["over"] > no_vig["under"]


def test_fair_odds_is_inverse_of_probability():
    assert fair_odds(0.5) == pytest.approx(2.0)
    assert fair_odds(0.25) == pytest.approx(4.0)


def test_edge_positive_when_model_more_confident_than_market():
    edge = compute_edge(model_probability=0.63, market_probability=0.55)
    assert edge == pytest.approx(0.08)


def test_edge_is_none_without_market_probability():
    assert compute_edge(0.63, None) is None


def test_expected_value_formula():
    # EV = p * odds - 1
    ev = compute_expected_value(model_probability=0.63, market_odds=1.82)
    assert ev == pytest.approx(0.63 * 1.82 - 1.0)


def test_expected_value_is_none_without_market_odds():
    assert compute_expected_value(0.63, None) is None
