from __future__ import annotations

from backend.app.config.settings import Settings
from backend.app.prediction.market_quality import dispersion_ratio, market_quality_tier

_SETTINGS = Settings(
    market_quality_high_min_bookmakers=10,
    market_quality_medium_min_bookmakers=4,
    market_quality_max_dispersion_ratio=0.15,
)


def test_no_bookmakers_is_low_quality():
    assert market_quality_tier(None, None, None, None, _SETTINGS) == "LOW"
    assert market_quality_tier(0, None, None, None, _SETTINGS) == "LOW"


def test_few_bookmakers_is_low_quality():
    assert market_quality_tier(1, 1.80, 1.85, 1.82, _SETTINGS) == "LOW"
    assert market_quality_tier(3, 1.80, 1.85, 1.82, _SETTINGS) == "LOW"


def test_medium_bookmakers_is_medium_quality():
    assert market_quality_tier(4, 1.80, 1.85, 1.82, _SETTINGS) == "MEDIUM"
    assert market_quality_tier(9, 1.80, 1.85, 1.82, _SETTINGS) == "MEDIUM"


def test_many_bookmakers_with_low_dispersion_is_high_quality():
    # dispersion = (1.85-1.80)/1.82 ~= 0.027, muy por debajo del umbral 0.15
    assert market_quality_tier(14, 1.80, 1.85, 1.82, _SETTINGS) == "HIGH"


def test_many_bookmakers_with_high_dispersion_is_downgraded_to_medium():
    """Feedback de usuario: 20+ casas no basta por si solo si estan muy en
    desacuerdo entre si (una casa outlier dispara el rango) -- el numero
    de casas por si solo no es suficiente evidencia de un consenso fiable."""
    # dispersion = (4.40-1.82)/1.84 ~= 1.40, muy por encima del umbral
    assert market_quality_tier(14, 1.82, 4.40, 1.84, _SETTINGS) == "MEDIUM"


def test_dispersion_ratio_formula():
    assert dispersion_ratio(1.80, 1.85, 1.82) == (1.85 - 1.80) / 1.82
    assert dispersion_ratio(None, 1.85, 1.82) is None
    assert dispersion_ratio(1.80, 1.85, 0) is None


def test_thresholds_are_configurable():
    lenient = Settings(market_quality_high_min_bookmakers=2, market_quality_medium_min_bookmakers=1)
    assert market_quality_tier(2, 1.80, 1.81, 1.805, lenient) == "HIGH"
