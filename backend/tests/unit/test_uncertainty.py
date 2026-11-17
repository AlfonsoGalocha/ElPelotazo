from __future__ import annotations

import pytest

from backend.app.features.goals import build_match_feature_table
from backend.app.models.goals.dixon_coles import DixonColesModel
from backend.app.prediction.predictor import predict_markets_for_table
from backend.app.prediction.uncertainty import (
    MIN_EFFECTIVE_SAMPLE_SIZE,
    compute_uncertainty_interval,
    wilson_interval,
)
from backend.tests.fixtures.synthetic import generate_synthetic_matches


def test_wilson_interval_is_always_within_unit_bounds():
    for p in (0.01, 0.5, 0.99):
        for n in (1, 5, 50, 1000):
            low, high = wilson_interval(p, n)
            assert 0.0 <= low <= p <= high <= 1.0


def test_wilson_interval_narrows_as_sample_size_grows():
    low_small, high_small = wilson_interval(0.6, 5)
    low_large, high_large = wilson_interval(0.6, 500)
    assert (high_small - low_small) > (high_large - low_large)


def test_compute_uncertainty_interval_uses_minimum_sample_size_when_missing():
    """Sin informacion de tamano de muestra (None o 0), se usa el minimo,
    nunca un intervalo estrecho que fingiria certeza inexistente."""
    with_none = compute_uncertainty_interval(0.6, None)
    with_zero = compute_uncertainty_interval(0.6, 0.0)
    with_min = compute_uncertainty_interval(0.6, MIN_EFFECTIVE_SAMPLE_SIZE)
    assert with_none == with_zero == with_min


def test_compute_uncertainty_interval_widens_with_model_disagreement():
    agree = compute_uncertainty_interval(0.6, 20.0, model_agreement=1.0)
    disagree = compute_uncertainty_interval(0.6, 20.0, model_agreement=0.0)
    assert (disagree[1] - disagree[0]) > (agree[1] - agree[0])


def test_compute_uncertainty_interval_ignores_missing_model_agreement():
    """Sin un segundo modelo con el que comparar (p.ej. mercados secundarios
    de tarjetas/corners, un unico Poisson): no se ensancha ni se penaliza
    por falta de dato."""
    no_agreement_info = compute_uncertainty_interval(0.6, 20.0, model_agreement=None)
    perfect_agreement = compute_uncertainty_interval(0.6, 20.0, model_agreement=1.0)
    assert no_agreement_info == perfect_agreement


@pytest.mark.parametrize("probability", [0.0, 0.05, 0.5, 0.95, 1.0])
def test_interval_never_leaves_unit_range_even_at_extremes(probability):
    low, high = compute_uncertainty_interval(probability, 3.0, model_agreement=0.0)
    assert 0.0 <= low <= high <= 1.0


def test_predict_markets_for_table_populates_uncertainty_bounds_end_to_end():
    """La funcion real que sirve produccion debe devolver un intervalo que
    contenga la propia probabilidad del modelo, nunca None (sin calibrador
    ni modelo ML, sigue habiendo informacion de tamano de muestra)."""
    matches = generate_synthetic_matches(n_teams=10, n_seasons=2, seed=99)
    table = build_match_feature_table(matches)
    model = DixonColesModel().fit(table)

    outputs = predict_markets_for_table(table.head(10), model)
    assert len(outputs) > 0
    for output in outputs:
        assert output.uncertainty_low is not None
        assert output.uncertainty_high is not None
        assert output.uncertainty_low <= output.model_probability <= output.uncertainty_high


def test_risk_adjusted_expected_value_is_none_without_market_odds():
    """Pedido real del usuario ("revision motor 2.0", Fase 4/5): nunca
    tratar `model_probability` como cifra fiable al 100% para el EV --
    `risk_adjusted_expected_value` usa `uncertainty_low` en su lugar. Sin
    cuota de mercado, igual que `expected_value`, debe quedar `None`."""
    matches = generate_synthetic_matches(n_teams=10, n_seasons=2, seed=99)
    table = build_match_feature_table(matches)
    model = DixonColesModel().fit(table)

    outputs = predict_markets_for_table(table.head(10), model)
    for output in outputs:
        assert output.expected_value is None
        assert output.risk_adjusted_expected_value is None


def test_risk_adjusted_expected_value_never_more_optimistic_than_expected_value():
    """`uncertainty_low <= model_probability` siempre -- con la misma
    cuota, el EV ajustado al riesgo nunca puede ser mayor que el EV
    normal."""
    matches = generate_synthetic_matches(n_teams=10, n_seasons=2, seed=99)
    table = build_match_feature_table(matches)
    model = DixonColesModel().fit(table)

    rows = table.head(10)
    market_quotes = {
        (int(row["match_id"]), market_key): {
            "market_probability": 0.4,
            "market_odds": 2.2,
            "vig_removed": True,
            "bookmakers_used": 5,
        }
        for _, row in rows.iterrows()
        for market_key in ("home_win", "draw", "away_win")
    }

    outputs = predict_markets_for_table(rows, model, market_quotes=market_quotes)
    assert any(o.expected_value is not None for o in outputs)
    for output in outputs:
        if output.expected_value is None:
            continue
        assert output.risk_adjusted_expected_value is not None
        assert output.risk_adjusted_expected_value <= output.expected_value + 1e-9
