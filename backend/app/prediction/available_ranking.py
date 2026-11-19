"""Relative ranking for valid prices. Value qualification is evaluated separately."""

import math

from backend.app.prediction.ranking import (
    calibrated_probability_or_fallback,
    conservative_probability,
    ev_with_probability,
    odds_age_minutes,
)


def rank_available(predictions, reliability, *, per_match=1):
    candidates = [
        p for p in predictions
        if p.market_odds is not None and 1.35 <= p.market_odds <= 1.90
        and p.market_probability_source != "derived_double_chance"
        and not p.market.startswith("cards_")
        and math.isfinite(calibrated_probability_or_fallback(p))
        and 0 <= calibrated_probability_or_fallback(p) <= 1
    ]

    def order(p):
        age = odds_age_minutes(p)
        return (
            conservative_probability(p),
            calibrated_probability_or_fallback(p),
            ev_with_probability(conservative_probability(p), p.market_odds),
            ev_with_probability(calibrated_probability_or_fallback(p), p.market_odds),
            reliability.get((p.match.competition.code, p.market), -1),
            p.data_quality,
            -age if age is not None else -math.inf,
            -p.id,
        )

    ranked = []
    by_match = {}
    for p in sorted(candidates, key=order, reverse=True):
        chosen = by_match.setdefault(p.match_id, set())
        # One selection per market family for additional picks in the same match.
        family = "corners" if p.market.startswith("corners_") else (
            "goals" if p.market.startswith(("over_", "under_")) else (
                "result" if p.market in {"home_win", "draw", "away_win"}
                or p.market.startswith("double_chance_") else p.market
            )
        )
        if len(chosen) < per_match and family not in chosen:
            chosen.add(family)
            ranked.append(p)
    return ranked
