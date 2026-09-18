from __future__ import annotations

from backend.app.services.prediction_service import _add_derived_double_chance_quotes


def _quote(probability: float, bookmakers_used: int = 3, vig_removed: bool = True) -> dict:
    return {
        "market_probability": probability,
        "market_odds": 1.0 / probability,
        "vig_removed": vig_removed,
        "market_probability_source": "consensus_no_vig" if vig_removed else "consensus_raw",
        "bookmakers_count": bookmakers_used,
        "bookmakers_used": bookmakers_used,
        "market_odds_min": None,
        "market_odds_max": None,
        "market_odds_median": None,
        "market_odds_average": None,
    }


def test_derives_double_chance_from_h2h_components():
    """La cuota de doble oportunidad NUNCA deberia faltar si hay cuota de
    h2h (a diferencia de btts/alternate_totals, que dependen de que una
    casa concreta ofrezca justo ese mercado): se deriva sumando las
    probabilidades sin vig ya calculadas para sus dos componentes."""
    market_quotes = {
        (1, "home_win"): _quote(0.50),
        (1, "draw"): _quote(0.30),
        (1, "away_win"): _quote(0.20),
    }
    _add_derived_double_chance_quotes(market_quotes, [1])

    assert (1, "double_chance_1x") in market_quotes
    assert (1, "double_chance_x2") in market_quotes
    assert (1, "double_chance_12") in market_quotes

    q_1x = market_quotes[(1, "double_chance_1x")]
    assert q_1x["market_probability"] == 0.80
    assert q_1x["market_odds"] == 1.0 / 0.80
    assert q_1x["market_probability_source"] == "derived_double_chance"

    q_x2 = market_quotes[(1, "double_chance_x2")]
    assert q_x2["market_probability"] == 0.50

    q_12 = market_quotes[(1, "double_chance_12")]
    assert q_12["market_probability"] == 0.70


def test_never_overwrites_a_real_quote_if_one_already_exists():
    """Si en el futuro alguna fuente trae "double_chance" como mercado
    propio, esa cuota REAL debe prevalecer sobre la derivada."""
    market_quotes = {
        (1, "home_win"): _quote(0.50),
        (1, "draw"): _quote(0.30),
        (1, "away_win"): _quote(0.20),
        (1, "double_chance_1x"): _quote(0.78, bookmakers_used=2),  # cuota "real" distinta de 0.80
    }
    _add_derived_double_chance_quotes(market_quotes, [1])
    assert market_quotes[(1, "double_chance_1x")]["market_probability"] == 0.78


def test_skips_when_a_component_quote_is_missing():
    """Sin cuota valida para AMBOS componentes, no se inventa una cuota de
    doble oportunidad a partir de solo uno de los dos."""
    market_quotes = {(1, "home_win"): _quote(0.50)}
    _add_derived_double_chance_quotes(market_quotes, [1])
    assert (1, "double_chance_1x") not in market_quotes
    assert (1, "double_chance_12") not in market_quotes
