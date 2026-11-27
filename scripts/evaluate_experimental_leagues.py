"""Train inactive candidates and grade a chronologically reserved set of fixtures."""

import argparse
from collections import defaultdict
from datetime import datetime
import json

from backend.app.db.database import session_scope
from backend.app.db.models.core import Competition
from backend.app.db.models.modeling import ModelVersion
from backend.app.features.goals import build_match_feature_table
from backend.app.normalization.competitions import EXPERIMENTAL_COMPETITIONS
from backend.app.prediction.market_labels import MARKET_DEFINITIONS, label_for_market
from backend.app.prediction.opportunities import valid_probability
from backend.app.prediction.probability import market_probabilities
from backend.app.prediction.secondary_markets import (
    SECONDARY_MARKET_DEFINITIONS, has_real_count_data, label_for_secondary_market,
)
from backend.app.services.corner_tracking import probability_scores
from backend.app.services.match_service import load_matches_dataframe
from backend.app.services.model_service import load_model_artifact, train_competition_models
from backend.app.services.prediction_service import _predict_fixture_markets


def evaluate(db, code, cutoff):
    result = train_competition_models(db, code, fit_before=cutoff, skip_unchanged=True)
    if "model_version_id" not in result:
        return result
    version = db.get(ModelVersion, result["model_version_id"])
    assert version.is_active is False and version.training_end < cutoff.date()
    artifact = load_model_artifact(version)
    competition = db.query(Competition).filter_by(code=code).one()
    data = load_matches_dataframe(db, competition.id)
    table = build_match_feature_table(data[data.status.eq("finished")].dropna(subset=["home_goals", "away_goals"]))
    target = table[table.date.ge(cutoff)].copy()
    if target.empty:
        return {**result, "evaluation_matches": 0}
    goal_reference = {market: dict(zip(target.match_id, p))
                      for market, p in market_probabilities(artifact["baseline"], target).items()}
    labels = {market: dict(zip(target.match_id, label_for_market(target, market)))
              for market in MARKET_DEFINITIONS}
    corners = target[has_real_count_data(target, "corners")]
    labels.update({market: dict(zip(corners.match_id, label_for_secondary_market(corners, market)))
                   for market in SECONDARY_MARKET_DEFINITIONS})
    scores = defaultdict(list)
    for row in _predict_fixture_markets(target, artifact, {}):
        actual = labels.get(row.market, {}).get(row.match_id)
        if actual is None:
            continue
        poisson = goal_reference.get(row.market, {}).get(row.match_id, row.model_probability)
        scores[row.market].append((actual, poisson, row.model_probability, row.calibrated_probability))
    metrics = {}
    for market, rows in scores.items():
        y, p, raw, _ = zip(*rows)
        paired = [(a, r, c) for a, _, r, c in rows if valid_probability(c)]
        metrics[market] = {"n": len(rows), "poisson": probability_scores(y, p),
                           "original": probability_scores(y, raw), "calibrated_n": len(paired)}
        if paired:
            y, raw, calibrated = zip(*paired)
            metrics[market]["paired_original"] = probability_scores(y, raw)
            metrics[market]["calibrated"] = probability_scores(y, calibrated)
    validation = {"competition": code, "status": result["status"], "candidate_id": version.id,
                  "version": version.version, "fit_before": cutoff.isoformat(),
                  "training_matches": result.get("n_matches", len(table) - len(target)),
                  "evaluation_matches": len(target), "corner_matches": len(corners), "metrics": metrics}
    version.metrics = {**(version.metrics or {}), "research_holdout": validation}
    return validation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout-from", type=datetime.fromisoformat, required=True)
    parser.add_argument("--competition", choices=EXPERIMENTAL_COMPETITIONS, required=True)
    args = parser.parse_args()
    with session_scope() as db:
        print(json.dumps(evaluate(db, args.competition, args.holdout_from)), flush=True)


if __name__ == "__main__":
    main()
