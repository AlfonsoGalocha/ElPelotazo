"""Evaluate fixed ranking policies on publications after an explicit calendar cut."""

import argparse
import datetime as dt
import json
import math
from collections import defaultdict

from sqlalchemy import text

from backend.app.db.database import session_scope
from backend.app.db.models.matches import Match
from backend.app.db.models.publication import FixturePublication
from backend.app.prediction.eligibility import observed_corner_total
from backend.app.services.publication_service import selection_outcome


def estimated_probability(candidate):
    calibrated = candidate["calibrated"]
    return candidate["probability"] if calibrated is None else calibrated


POLICIES = {
    "estimated_probability": estimated_probability,
    "conservative_probability": lambda item: item["conservative"],
    "conservative_ev": lambda item: item["conservative_ev"],
    "relative_v1": lambda item: -item["position"],
    "hybrid": lambda item: (item["conservative_ev"] > 0, estimated_probability(item)),
}


def eligible_at_capture(candidate, captured_at):
    """Only replay the archived execution quote and probability known at the cut."""
    if (candidate.get("reasons") != [] or not candidate.get("quote_history_id")
            or candidate.get("calibrated") is None or candidate.get("opportunity_score") is None):
        return False
    try:
        origin = dt.datetime.fromisoformat(candidate["odds_origin_at"])
        fetched = dt.datetime.fromisoformat(candidate["odds_fetched_at"])
        recorded = dt.datetime.fromisoformat(candidate["odds_recorded_at"])
        return origin <= fetched <= recorded <= captured_at and candidate["odds"] > 1
    except (KeyError, TypeError, ValueError):
        return False


def probability_heavy(candidate):
    probability, ev = candidate["calibrated"], min(max(candidate["estimated_ev"] / .20, 0), 1)
    evidence = (candidate["opportunity_score"] - .4 * probability - .3 * ev) / .3
    return .6 * probability + .2 * ev + .2 * evidence


EXECUTION_POLICIES = {
    "composite_40_30_30": lambda item: item["opportunity_score"],
    "composite_60_20_20": probability_heavy,
    "calibrated_probability": lambda item: item["calibrated"],
    "estimated_ev": lambda item: item["estimated_ev"],
}


def summarize(rows):
    realized, peak, drawdown = 0.0, 0.0, 0.0
    brier, loss, wins = 0.0, 0.0, 0
    for candidate, actual in rows:
        probability = estimated_probability(candidate)
        wins += int(actual)
        realized += candidate["odds"] - 1 if actual else -1
        peak = max(peak, realized)
        drawdown = max(drawdown, peak - realized)
        brier += (probability - actual) ** 2
        loss -= math.log(max(1e-15, probability if actual else 1-probability))
    n = len(rows)
    bins = defaultdict(list)
    for candidate, actual in rows:
        probability = estimated_probability(candidate)
        bins[min(int(10 * probability), 9)].append((probability, actual))
    calibration = [
        {"low": bucket / 10, "high": (bucket + 1) / 10, "n": len(values),
         "predicted": sum(p for p, _ in values) / len(values),
         "observed": sum(y for _, y in values) / len(values)}
        for bucket, values in sorted(bins.items())
    ]
    return {"n": n, "accuracy": wins/n if n else None, "roi": realized/n if n else None,
            "yield": realized/n if n else None, "calibration": calibration,
            "brier": brier/n if n else None, "log_loss": loss/n if n else None,
            "drawdown": drawdown if n else None}


def compare(db, start, end, execution=False):
    query = db.query(FixturePublication, Match, observed_corner_total()).join(
        Match, Match.id == FixturePublication.match_id
    ).filter(
        FixturePublication.captured_at < Match.kickoff_utc,
        Match.status == "finished", Match.kickoff_utc >= start, Match.kickoff_utc < end,
    ).order_by(Match.kickoff_utc, Match.id)
    policies = EXECUTION_POLICIES if execution else POLICIES
    samples = {name: [] for name in policies}
    slices = {name: defaultdict(list) for name in policies}
    for publication, match, total in query:
        if execution:
            if publication.ranking_version not in {"execution-v2", "execution-v3"} or publication.captured_at >= publication.scheduled_kickoff:
                continue
            candidates = [row for row in publication.ranking if eligible_at_capture(row, publication.captured_at)]
        else:
            candidates = publication.ranking if publication.ranking_version not in {"execution-v2", "execution-v3"} else []
        if not candidates:
            continue
        stats = match.statistics
        if total is None and stats and stats.home_corners is not None and stats.away_corners is not None:
            if min(stats.home_corners, stats.away_corners) >= 0:
                total = stats.home_corners + stats.away_corners
        for name, key in policies.items():
            candidate = max(candidates, key=key)
            actual = selection_outcome(candidate["market"], match.home_goals, match.away_goals, total)
            if actual is not None:
                pair = (candidate, actual)
                samples[name].append(pair)
                slices[name][("liga", match.competition.code)].append(pair)
                slices[name][("mercado", candidate["market"])].append(pair)
                if execution:
                    band = "1.35–1.49" if candidate["odds"] < 1.50 else "1.50–1.69" if candidate["odds"] < 1.70 else "1.70–1.90"
                    slices[name][("cuota", band)].append(pair)
                    slices[name][("confianza", "alta" if candidate["high_confidence"] else "limitada")].append(pair)
    return {
        "kind": "historical_counterfactual_on_frozen_candidates",
        "strategies": {
            name: {**summarize(rows), "segments": [
                {"dimension": dimension, "group": group, **summarize(values)}
                for (dimension, group), values in sorted(slices[name].items())
            ]}
            for name, rows in samples.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--before-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--execution", action="store_true")
    args = parser.parse_args()
    if args.from_date >= args.before_date:
        parser.error("Se requiere un intervalo cronológico positivo")
    with session_scope() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        report = compare(
            db, dt.datetime.combine(args.from_date, dt.time.min),
            min(dt.datetime.utcnow(), dt.datetime.combine(args.before_date, dt.time.min)),
            execution=args.execution,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
