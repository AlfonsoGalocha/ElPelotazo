"""Evaluate fixed model/market blends on chronological splits of frozen forecasts."""

import argparse
import datetime as dt
import json
from collections import Counter, defaultdict

import numpy as np
from sqlalchemy import text

from backend.app.db.database import SessionLocal
from backend.app.market.prematch_reference import no_vig_reference
from backend.app.prediction.market_labels import DOUBLE_CHANCE_COMPONENTS
from backend.app.services.corner_tracking import probability_scores
from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP
from scripts.audit_frozen_market_reference import paired_forecasts

WEIGHTS = (1., .75, .50, .25, 0.)
RESULT = ("home_win", "draw", "away_win")
COMPLEMENTS = (("over_2_5", "under_2_5"), ("corners_over_9_5", "corners_under_9_5"))
TOTALS = (("over_1_5", "over_2_5", "over_3_5"),
          ("corners_over_8_5", "corners_over_9_5", "corners_over_10_5"))
MIN_SEGMENT = 30


def coherent_reference(snapshot, market):
    if market in RESULT or market in DOUBLE_CHANCE_COMPONENTS:
        probabilities = [no_vig_reference(snapshot, ALL_MARKET_TO_ODDS_LOOKUP[key]) for key in RESULT]
        if any(p is None for p in probabilities):
            return None
        components = DOUBLE_CHANCE_COMPONENTS.get(market, (market,))
        return sum(p for key, p in zip(RESULT, probabilities) if key in components) / sum(probabilities)
    over = next((over for over, under in COMPLEMENTS if under == market), None)
    p = no_vig_reference(snapshot, ALL_MARKET_TO_ODDS_LOOKUP[over or market])
    return 1 - p if over and p is not None else p


def coherence_checks(values):
    def present(keys):
        return all(key in values for key in keys)

    close = lambda a, b: abs(a - b) <= 1e-8
    if present(RESULT):
        yield "1x2_sum", close(sum(values[k] for k in RESULT), 1)
    for market, components in DOUBLE_CHANCE_COMPONENTS.items():
        if present((market, *components)):
            yield "double_chance", close(values[market], sum(values[k] for k in components))
    for name, (over, under), totals in zip(("goals", "corners"), COMPLEMENTS, TOTALS):
        if present((over, under)):
            yield f"{name}_complements", close(values[over] + values[under], 1)
        for smaller, larger in zip(totals, totals[1:]):
            if present((smaller, larger)):
                yield f"{name}_monotonicity", values[smaller] + 1e-8 >= values[larger]


def evaluate(rows, boundary):
    cohorts, totals, by_cut = defaultdict(list), Counter(), defaultdict(dict)
    for match_id, kickoff, cutoff, code, market, actual, model, reference in rows:
        period = "validation" if kickoff < boundary else "evaluation"
        for scope in ("all", code):
            totals[period, scope, market] += 1
            if reference is not None:
                cohorts[period, scope, market].append((actual, model, reference))
        if reference is not None:
            by_cut[period, match_id, cutoff][market] = model, reference

    for (period, scope, market), total in sorted(totals.items()):
        pairs = cohorts[period, scope, market]
        result = dict(period=period, competition=scope, market=market, evaluable=total,
                      paired=len(pairs), coverage=len(pairs) / total, sufficient_sample=len(pairs) >= MIN_SEGMENT)
        if pairs and (scope == "all" or len(pairs) >= MIN_SEGMENT):
            y, model, market_p = map(np.asarray, zip(*pairs))
            result["weights"] = {weight: probability_scores(y, weight * model + (1 - weight) * market_p)
                                 for weight in WEIGHTS}
        yield result

    coherence = defaultdict(Counter)
    for (period, _, _), markets in by_cut.items():
        for weight in WEIGHTS:
            values = {key: weight * model + (1 - weight) * reference
                      for key, (model, reference) in markets.items()}
            for check, passed in coherence_checks(values):
                coherence[period, weight, check]["checked"] += 1
                coherence[period, weight, check]["failed"] += not passed
    for (period, weight, check), result in sorted(coherence.items()):
        yield dict(period=period, model_weight=weight, coherence=check, **result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-from", type=dt.date.fromisoformat, required=True)
    args = parser.parse_args()
    boundary = dt.datetime.combine(args.evaluation_from, dt.time.min)
    with SessionLocal() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        for report in evaluate(paired_forecasts(db, reference_for=coherent_reference), boundary):
            print(json.dumps(report))


if __name__ == "__main__":
    main()
