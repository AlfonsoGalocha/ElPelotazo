"""Compare prematch model, closing market and a blend on held-out seasons."""

import json
from collections import defaultdict

import numpy as np
from sqlalchemy import text

from backend.app.backtesting.splits import expanding_window_splits
from backend.app.db.database import SessionLocal
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.features.goals import build_match_feature_table
from backend.app.market.consensus import compute_market_consensus
from backend.app.models.calibration.metrics import brier_score, expected_calibration_error, log_loss_score
from backend.app.models.goals.poisson_model import PoissonRegressionModel
from backend.app.prediction.market_labels import label_for_market
from backend.app.prediction.probability import market_probabilities
from backend.app.services.match_service import load_matches_dataframe

LEAGUES = ("laliga", "premier_league", "bundesliga", "serie_a", "ligue_1")
SEASONS = tuple(f"{year}/{(year + 1) % 100:02d}" for year in range(2020, 2026))
MARKETS = {
    "home_win": ("match_result", None, "home", {"home", "draw", "away"}),
    "over_2_5": ("over_under_goals", 2.5, "over", {"over", "under"}),
}


def market_reference(rows, market, line, selection, required):
    by_book = defaultdict(list)
    for row in rows:
        if row.market == market and row.line == line and row.selection in required:
            by_book[row.bookmaker].append(row)
    complete = [
        row for quotes in by_book.values() if {row.selection for row in quotes} == required
        for row in quotes
    ]
    return compute_market_consensus(complete, market, line, selection).market_probability


def collect_folds(db):
    samples = defaultdict(list)
    for league in db.query(Competition).filter(Competition.code.in_(LEAGUES)):
        matches = load_matches_dataframe(db, league.id)
        matches = matches[matches.season_label.isin(SEASONS) & (matches.status == "finished")]
        table = build_match_feature_table(matches.dropna(subset=["home_goals", "away_goals"]))
        odds = defaultdict(list)
        for row in db.query(MatchOdds).join(Match).filter(
            Match.competition_id == league.id,
            MatchOdds.snapshot_type == "closing",
            MatchOdds.price > 1,
            MatchOdds.market.in_(("match_result", "over_under_goals")),
        ).order_by(MatchOdds.id):
            odds[row.match_id].append(row)
        for fold in expanding_window_splits(table):
            train = table.loc[fold.train_index]
            test = table.loc[fold.test_index]
            train = train[train.date < test.date.min()]
            if train.empty:
                continue
            model = PoissonRegressionModel().fit(train)
            probabilities = market_probabilities(model, test)
            for key, spec in MARKETS.items():
                reference = test.match_id.map(lambda match_id: market_reference(odds[match_id], *spec))
                valid = reference.notna().to_numpy()
                labels = label_for_market(test, key).to_numpy()
                samples[key, fold.test_season].extend(
                    zip(labels[valid], probabilities[key][valid], reference.to_numpy(dtype=float)[valid])
                )
    return samples


def metrics(labels, probabilities):
    return {
        "brier": round(brier_score(labels, probabilities), 6),
        "log_loss": round(log_loss_score(labels, probabilities), 6),
        "ece": round(expected_calibration_error(labels, probabilities), 6),
    }


def main():
    with SessionLocal() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        samples = collect_folds(db)
    for market in MARKETS:
        past = []
        for season in SEASONS:
            current = samples[market, season]
            if not current:
                continue
            if past:
                y_past, p_past, m_past = map(np.asarray, zip(*past))
                weight = min(
                    np.linspace(0, 1, 11),
                    key=lambda w: log_loss_score(y_past, w * p_past + (1 - w) * m_past),
                )
                y, p, m = map(np.asarray, zip(*current))
                print(json.dumps({
                    "market": market, "season": season, "n": len(y), "model_weight": weight,
                    "model": metrics(y, p), "market_reference": metrics(y, m),
                    "blend": metrics(y, weight * p + (1 - weight) * m),
                }))
            past.extend(current)


if __name__ == "__main__":
    main()
