"""Score archived match forecasts against timestamped, complete no-vig quotes."""

from collections import defaultdict
import json

import numpy as np
from sqlalchemy import text

from backend.app.db.database import SessionLocal
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match, MatchOddsHistory, MatchStatistics
from backend.app.db.models.modeling import ModelVersion, PredictionSnapshot
from backend.app.db.models.publication import FixturePublication, FixtureRankingRevision, PreMatchForecast
from backend.app.market.prematch_reference import no_vig_reference, snapshot_markets
from backend.app.prediction.eligibility import observed_corner_total
from backend.app.prediction.market_labels import DOUBLE_CHANCE_COMPONENTS
from backend.app.prediction.opportunities import valid_probability
from backend.app.services.corner_tracking import probability_scores
from backend.app.services.prediction_service import ALL_MARKET_TO_ODDS_LOOKUP
from backend.app.services.publication_service import selection_outcome


def record_forecasts(forecasts, match, code, corners, stats, cutoff, rows):
    if corners is None and stats:
        home, away = stats.home_corners, stats.away_corners
        if home is not None and away is not None and min(home, away) >= 0:
            corners = home + away
    for market, probability in rows:
        if market not in ALL_MARKET_TO_ODDS_LOOKUP or not valid_probability(probability):
            continue
        older = forecasts[match.id].get(market)
        if older and cutoff <= older[0]:
            continue
        outcome = selection_outcome(market, match.home_goals, match.away_goals, corners)
        if outcome is not None:
            forecasts[match.id][market] = cutoff, code, probability, outcome


def market_reference(snapshot, market):
    if market in DOUBLE_CHANCE_COMPONENTS:
        # A double-chance event is the complement of one 1X2 outcome.
        omitted = next(key for key in ("home_win", "draw", "away_win")
                       if key not in DOUBLE_CHANCE_COMPONENTS[market])
        reference = no_vig_reference(snapshot, ALL_MARKET_TO_ODDS_LOOKUP[omitted])
        return 1 - reference if reference is not None else None
    return no_vig_reference(snapshot, ALL_MARKET_TO_ODDS_LOOKUP[market])


def paired_forecasts(db, reference_for=market_reference):
    forecasts = defaultdict(dict)
    kickoffs = {}
    for source in (FixturePublication, FixtureRankingRevision, PreMatchForecast):
        query = db.query(source, Match, Competition.code, observed_corner_total(), MatchStatistics).join(
            Match, Match.id == source.match_id,
        ).join(Competition, Competition.id == Match.competition_id).outerjoin(
            MatchStatistics, MatchStatistics.match_id == Match.id,
        ).filter(Match.status == "finished", source.captured_at < Match.kickoff_utc)
        if source is not PreMatchForecast:
            query = query.filter(source.captured_at < source.scheduled_kickoff)
        for capture, match, code, corners, stats in query:
            kickoffs[match.id] = match.kickoff_utc
            if source is PreMatchForecast:
                rows = ((market, details.get("probability")) for market, details in
                        (capture.payload.get("markets") or {}).items())
            else:
                rows = ((row["market"], row.get("calibrated") if row.get("calibrated") is not None
                         else row.get("probability")) for row in capture.ranking)
            record_forecasts(forecasts, match, code, corners, stats, capture.captured_at, rows)

    published = {match_id: set(rows) for match_id, rows in forecasts.items()}
    snapshots = db.query(PredictionSnapshot, Match, Competition.code, observed_corner_total(), MatchStatistics).join(
        Match, Match.id == PredictionSnapshot.match_id,
    ).join(Competition, Competition.id == Match.competition_id).join(
        ModelVersion, ModelVersion.id == PredictionSnapshot.model_version_id,
    ).outerjoin(MatchStatistics, MatchStatistics.match_id == Match.id).filter(
        Match.status == "finished",
        ModelVersion.created_at <= PredictionSnapshot.created_at,
        PredictionSnapshot.created_at <= PredictionSnapshot.archived_at,
        PredictionSnapshot.archived_at < Match.kickoff_utc,
    )
    for row, match, code, corners, stats in snapshots:
        kickoffs[match.id] = match.kickoff_utc
        market = row.market
        if market in published.get(match.id, ()):
            continue
        key = ALL_MARKET_TO_ODDS_LOOKUP.get(market)
        if not key or key[1:] != (row.line, row.selection):
            continue
        probability = row.calibrated_probability if row.calibrated_probability is not None else row.model_probability
        record_forecasts(forecasts, match, code, corners, stats, row.created_at, [(market, probability)])

    quotes = defaultdict(list)
    for quote in db.query(MatchOddsHistory).join(Match).filter(
        Match.id.in_(forecasts), MatchOddsHistory.snapshot_type == "pre_match",
        MatchOddsHistory.recorded_at < Match.kickoff_utc,
    ).yield_per(5000):
        quotes[quote.match_id].append(quote)
    for match_id, rows in forecasts.items():
        snapshots = {}
        for market, (cutoff, code, probability, outcome) in rows.items():
            if cutoff not in snapshots:
                snapshots[cutoff] = snapshot_markets(quotes[match_id], cutoff)
            reference = reference_for(snapshots[cutoff], market)
            yield match_id, kickoffs[match_id], cutoff, code, market, outcome, probability, reference


def collect(db):
    totals, pairs = defaultdict(int), defaultdict(list)
    for _, _, _, code, market, outcome, probability, reference in paired_forecasts(db):
        for scope in ("all", code):
            totals[scope, market] += 1
            if reference is not None:
                pairs[scope, market].append((outcome, probability, reference))
    return totals, pairs


def summarize(totals, pairs):
    for (scope, market), total in sorted(totals.items()):
        rows = pairs[scope, market]
        report = dict(competition=scope, market=market, evaluable=total, paired=len(rows),
                      reference_source="derived_1x2_no_vig" if market in DOUBLE_CHANCE_COMPONENTS else "no_vig")
        if rows:
            y, model, reference = map(np.asarray, zip(*rows))
            # Each market contributes one row per fixture, so this is a paired
            # fixture bootstrap on the log-loss difference.
            y_prob = lambda p: np.where(y, p, 1 - p).clip(1e-6, 1 - 1e-6)
            delta = np.log(y_prob(reference)) - np.log(y_prob(model))
            rng = np.random.default_rng(42)
            interval = np.quantile([rng.choice(delta, len(delta)).mean() for _ in range(2000)], [.025, .975])
            report.update(model=probability_scores(y, model), market_reference=probability_scores(y, reference),
                          delta_log_loss_95=interval.tolist())
        yield report


def main():
    with SessionLocal() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        totals, pairs = collect(db)
    for report in summarize(totals, pairs):
        print(json.dumps(report))


if __name__ == "__main__":
    main()
