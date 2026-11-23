"""Relative ranking for valid prices. Value qualification is evaluated separately."""

import math

from backend.app.config.settings import get_settings

from backend.app.prediction.ranking import (
    calibrated_probability_or_fallback,
    conservative_probability,
    ev_with_probability,
    odds_age_minutes,
)


def has_available_price(prediction, *, all_prices=False, odds_band=None):
    settings = get_settings()
    floor, ceiling = odds_band or (settings.min_opportunity_odds, settings.max_opportunity_odds)
    price = prediction.market_odds
    return (
        price is not None and math.isfinite(price) and price > 1
        and (all_prices or floor <= price <= ceiling)
        and prediction.market_probability_source != "derived_double_chance"
        and not prediction.market.startswith("cards_")
    )


def rank_available(predictions, reliability, *, per_match=1, limit=None, all_prices=False, value_ids=frozenset(), prefer_calibrated=False, odds_band=None):
    candidates = [
        p for p in predictions
        if has_available_price(p, all_prices=all_prices, odds_band=odds_band)
        and math.isfinite(calibrated_probability_or_fallback(p))
        and 0 <= calibrated_probability_or_fallback(p) <= 1
    ]

    def order(p):
        age = odds_age_minutes(p)
        return (
            p.id in value_ids,
            prefer_calibrated and p.calibrated_probability is not None,
            conservative_probability(p),
            calibrated_probability_or_fallback(p),
            ev_with_probability(conservative_probability(p), p.market_odds),
            ev_with_probability(calibrated_probability_or_fallback(p), p.market_odds),
            reliability.get((p.match.competition.code, p.market), -1),
            p.data_quality,
            -age if age is not None else -math.inf,
            -p.id,
        )

    def family_for(p):
        if p.market.startswith("corners_"):
            return "corners"
        if p.market.startswith(("over_", "under_")):
            return "goals"
        if p.market in {"home_win", "draw", "away_win"} or p.market.startswith("double_chance_"):
            return "result"
        return p.market

    ranked = []
    by_match = {}
    family_counts = {}
    postponed = []
    family_budget = math.ceil(limit / 3) if limit is not None else None

    def add(p, family):
        chosen = by_match.setdefault(p.match_id, set())
        if len(chosen) >= per_match or family in chosen:
            return
        chosen.add(family)
        family_counts[family] = family_counts.get(family, 0) + 1
        ranked.append(p)

    for p in sorted(candidates, key=order, reverse=True):
        family = family_for(p)
        if p.id not in value_ids and family_budget is not None and family_counts.get(family, 0) >= family_budget:
            postponed.append((p, family))
        else:
            add(p, family)
        if limit is not None and len(ranked) == limit:
            return ranked
    for p, family in postponed:
        add(p, family)
        if len(ranked) == limit:
            break
    return ranked
