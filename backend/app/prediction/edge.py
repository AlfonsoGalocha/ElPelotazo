"""Edge estadistico y valor esperado (EV) — seccion 15/16 del brief.

edge_probability = model_probability - market_probability   (SIN vig cuando sea posible)
expected_value   = model_probability * market_odds - 1

Un Signal encapsula todo lo necesario para presentar una oportunidad sin
mezclar probabilidad, edge, EV, confianza y calidad de dato (seccion 72:
nunca confundir estos conceptos).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Signal:
    match_id: int
    market: str
    selection: str
    model_probability: float
    market_probability: float | None
    market_odds: float | None
    fair_odds: float
    edge: float | None
    expected_value: float | None
    confidence: float
    data_quality: float
    vig_removed: bool | None


def compute_edge(model_probability: float, market_probability: float | None) -> float | None:
    if market_probability is None:
        return None
    return model_probability - market_probability


def compute_expected_value(model_probability: float, market_odds: float | None) -> float | None:
    if market_odds is None:
        return None
    return model_probability * market_odds - 1.0
