from __future__ import annotations

from backend.app.config.settings import Settings
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.ranking import ExclusionReason, evaluate_quality_gate, rank_signals, signal_score


def _prediction(**overrides) -> Prediction:
    defaults = dict(
        match_id=1,
        market="over_2_5",
        line=2.5,
        selection="over",
        model_version_id=1,
        model_probability=0.7,
        market_probability=0.55,
        market_odds=1.82,
        fair_odds=1.43,
        edge=0.15,
        expected_value=0.274,
        confidence=0.8,
        data_quality=0.8,
        bookmakers_used=4,
        bookmakers_count=4,
        explanation={"factors": []},
        features_used={"feature_names": []},
    )
    defaults.update(overrides)
    return Prediction(**defaults)


def test_prediction_without_market_is_excluded():
    prediction = _prediction(market_probability=None, market_odds=None, edge=None)
    assert evaluate_quality_gate(prediction) == ExclusionReason.NO_MARKET


def test_prediction_with_invalid_odds_is_excluded():
    prediction = _prediction(market_odds=1.0)
    assert evaluate_quality_gate(prediction) == ExclusionReason.INVALID_ODDS


def test_prediction_with_negative_edge_is_excluded():
    prediction = _prediction(edge=-0.05)
    assert evaluate_quality_gate(prediction) == ExclusionReason.INVALID_EDGE


def test_prediction_with_missing_edge_is_excluded():
    prediction = _prediction(edge=None)
    assert evaluate_quality_gate(prediction) == ExclusionReason.INVALID_EDGE


def test_prediction_with_too_few_bookmakers_is_excluded():
    settings = Settings(min_bookmakers=3)
    prediction = _prediction(bookmakers_used=1)
    assert evaluate_quality_gate(prediction, settings) == ExclusionReason.INSUFFICIENT_BOOKMAKERS


def test_prediction_passing_all_checks_is_not_excluded():
    prediction = _prediction()
    assert evaluate_quality_gate(prediction) is None


def test_score_of_extreme_probability_tiny_edge_is_near_zero():
    """98% de probabilidad a cuota 1.02 (mercado tambien ~98%): edge casi
    nulo. No deberia puntuar alto solo por la probabilidad altisima."""
    near_certain_low_value = _prediction(
        model_probability=0.98, market_probability=0.975, market_odds=1.02, edge=0.005, confidence=0.8,
        data_quality=0.8,
    )
    solid_value = _prediction(
        model_probability=0.80, market_probability=0.70, market_odds=1.35, edge=0.10, confidence=0.8,
        data_quality=0.8,
    )
    assert signal_score(near_certain_low_value) < signal_score(solid_value)


def test_score_keeps_high_probability_and_mediocre_high_edge_in_comparable_range():
    """El ejemplo del usuario: 80% a cuota 1.30-1.40 (edge~10pp) debe
    quedar en un orden de magnitud COMPARABLE a una jugada mediocre tipo
    55% a cuota 3.0 con mas puntos de edge en bruto (edge~22pp), gracias al
    cuadrado de la probabilidad — sin el cuadrado, el edge en puntos
    porcentuales domina completamente el ranking (ver el caso sin elevar
    al cuadrado, comentado abajo), dejando la jugada de 80% muy por debajo
    pese a ser la mas solida de las dos. Con el cuadrado, quedan a menos
    de un 10% de diferencia entre si en vez de una diferencia de varias
    veces."""
    solid_value = _prediction(model_probability=0.80, edge=0.10, confidence=0.8, data_quality=0.8)
    mediocre_high_edge = _prediction(model_probability=0.55, edge=0.22, confidence=0.8, data_quality=0.8)

    ratio = signal_score(mediocre_high_edge) / signal_score(solid_value)
    assert ratio < 1.10  # comparable, no un dominio aplastante del edge en bruto

    # Sin el cuadrado (probabilidad lineal), el edge en bruto domina mucho
    # mas: 0.55*0.22=0.121 vs 0.80*0.10=0.08 -> ratio ~1.51, muy por encima
    # del ratio ~1.04 que da el cuadrado.
    linear_ratio = (0.55 * 0.22) / (0.80 * 0.10)
    assert linear_ratio > ratio


def test_rank_signals_separates_included_and_excluded_with_reasons():
    good = _prediction(match_id=1)
    no_market = _prediction(match_id=2, market_probability=None, market_odds=None, edge=None)
    bad_odds = _prediction(match_id=3, market_odds=1.0)

    included, excluded = rank_signals([good, no_market, bad_odds])

    assert len(included) == 1
    assert included[0].prediction is good
    assert {e.reason for e in excluded} == {ExclusionReason.NO_MARKET, ExclusionReason.INVALID_ODDS}


def test_rank_signals_orders_by_score_descending():
    low = _prediction(match_id=1, model_probability=0.98, market_probability=0.975, market_odds=1.02, edge=0.005)
    high = _prediction(match_id=2, model_probability=0.80, market_probability=0.70, market_odds=1.35, edge=0.10)

    included, _ = rank_signals([low, high])

    assert [s.prediction.match_id for s in included] == [2, 1]
