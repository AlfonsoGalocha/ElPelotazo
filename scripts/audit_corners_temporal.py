"""Compare corner count distributions on a fixed chronological holdout from stored results."""

from __future__ import annotations

import json

import numpy as np
from scipy.stats import nbinom, poisson
from sqlalchemy import text

from backend.app.db.database import SessionLocal
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.features.goals import build_match_feature_table
from backend.app.models.calibration.metrics import brier_score, expected_calibration_error, log_loss_score
from backend.app.prediction.secondary_markets import (
    SECONDARY_MARKET_DEFINITIONS,
    TotalCountPoissonModel,
    compute_total_column,
    has_real_count_data,
    label_for_secondary_market,
)
from backend.app.services.match_service import load_matches_dataframe


def measure(y, probability):
    return {
        "n": len(y),
        "brier": round(brier_score(y, probability), 6),
        "log_loss": round(log_loss_score(y, probability), 6),
        "ece": round(expected_calibration_error(y, probability), 6),
        "accuracy": round(float(np.mean((probability >= 0.5) == y)), 6),
    }


def compare(matches):
    if matches.empty:
        return None
    matches = matches[matches["status"] == "finished"].dropna(subset=["home_goals", "away_goals"])
    if matches.empty:
        return None
    table = build_match_feature_table(matches.sort_values("date"))
    observed = table[has_real_count_data(table, "corners")]
    if len(observed) < 80:
        return None
    split = observed.iloc[int(len(observed) * 0.7)]["date"]
    train = observed[observed["date"] < split]
    test = observed[observed["date"] >= split]
    if len(train) < 40 or len(test) < 20:
        return None
    model = TotalCountPoissonModel("corners").fit(train)
    mu_train = model.predict_lambda(train)
    count_train = compute_total_column(train, "corners").to_numpy()
    dispersion = max(0, float(np.sum((count_train - mu_train) ** 2 - count_train) / np.sum(mu_train**2)))
    mu_test = model.predict_lambda(test)
    markets = {}
    for market, spec in SECONDARY_MARKET_DEFINITIONS.items():
        y = label_for_secondary_market(test, market).to_numpy(dtype=bool)
        poisson_under = poisson.cdf(np.floor(spec.line), mu_test)
        nb_under = (
            nbinom.cdf(np.floor(spec.line), 1.0 / dispersion, 1.0 / (1.0 + dispersion * mu_test))
            if dispersion > 0 else poisson_under
        )
        if spec.kind == "over":
            poisson_under = 1 - poisson_under
            nb_under = 1 - nb_under
        markets[market] = {
            "poisson": measure(y, poisson_under),
            "negative_binomial": measure(y, nb_under),
        }
    return {
        "n_train": len(train),
        "n_test": len(test),
        "first_test": split.isoformat(),
        "last_test": test["date"].max().isoformat(),
        "dispersion": round(dispersion, 6),
        "markets": markets,
    }


def main():
    with SessionLocal() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        for competition in db.query(Competition).order_by(Competition.code):
            matches = load_matches_dataframe(db, competition.id)
            if not matches.empty and "status" not in matches:
                statuses = dict(db.query(Match.id, Match.status).filter_by(competition_id=competition.id).all())
                matches["status"] = matches["match_id"].map(statuses)
            report = compare(matches)
            if report is not None:
                print(json.dumps({"competition": competition.code, **report}))


if __name__ == "__main__":
    main()
