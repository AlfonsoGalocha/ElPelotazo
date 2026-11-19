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
    return {"n": n, "accuracy": wins/n if n else None, "roi": realized/n if n else None,
            "brier": brier/n if n else None, "log_loss": loss/n if n else None,
            "drawdown": drawdown if n else None}


def compare(db, start, end):
    query = db.query(FixturePublication, Match, observed_corner_total()).join(
        Match, Match.id == FixturePublication.match_id
    ).filter(
        FixturePublication.captured_at < Match.kickoff_utc,
        Match.status == "finished", Match.kickoff_utc >= start, Match.kickoff_utc < end,
    ).order_by(Match.kickoff_utc, Match.id)
    samples = {name: [] for name in POLICIES}
    slices = {name: defaultdict(list) for name in POLICIES}
    for publication, match, total in query:
        if not publication.ranking:
            continue
        stats = match.statistics
        if total is None and stats and stats.home_corners is not None and stats.away_corners is not None:
            if min(stats.home_corners, stats.away_corners) >= 0:
                total = stats.home_corners + stats.away_corners
        for name, key in POLICIES.items():
            candidate = max(publication.ranking, key=key)
            actual = selection_outcome(candidate["market"], match.home_goals, match.away_goals, total)
            if actual is not None:
                pair = (candidate, actual)
                samples[name].append(pair)
                slices[name][(match.competition.code, candidate["market"])].append(pair)
    return {
        "kind": "historical_counterfactual_on_frozen_candidates",
        "strategies": {
            name: {**summarize(rows), "segments": [
                {"competition": league, "market": market, **summarize(values)}
                for (league, market), values in sorted(slices[name].items())
            ]}
            for name, rows in samples.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--before-date", type=dt.date.fromisoformat, required=True)
    args = parser.parse_args()
    if args.from_date >= args.before_date:
        parser.error("Se requiere un intervalo cronológico positivo")
    with session_scope() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        report = compare(
            db, dt.datetime.combine(args.from_date, dt.time.min),
            min(dt.datetime.utcnow(), dt.datetime.combine(args.before_date, dt.time.min)),
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
