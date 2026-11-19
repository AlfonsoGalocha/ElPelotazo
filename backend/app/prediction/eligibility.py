"""Evidence gates for live predictions and their observed outcomes."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func, or_, select

from backend.app.db.models.matches import CornerObservation, Match, MatchStatistics
from backend.app.db.models.modeling import Prediction, PredictionResult
from backend.app.prediction.market_labels import MARKET_DEFINITIONS
from backend.app.prediction.secondary_markets import SECONDARY_MARKET_DEFINITIONS


def active_market():
    return Prediction.market.in_(tuple(MARKET_DEFINITIONS) + tuple(SECONDARY_MARKET_DEFINITIONS))


def stored_pre_kickoff():
    """Necessary timestamp check. This does not certify input or model provenance."""
    return Prediction.created_at < Match.kickoff_utc


def recorded_origin(prediction, match: Match | None, now: datetime) -> str:
    if match is None:
        return "unverified"
    if prediction.created_at >= match.kickoff_utc:
        return "post_kickoff_unverified"
    if prediction.feature_set_version is None:
        return "unverified"
    if match.status == "scheduled" and now < match.kickoff_utc:
        return "current_pre_kickoff_unverified"
    return "pre_kickoff_unverified"


def observed_corner_total():
    return (
        select(CornerObservation.total)
        .where(CornerObservation.match_id == Match.id)
        .order_by(CornerObservation.id.desc()).limit(1)
        .correlate(Match).scalar_subquery()
    )


def corner_verification(
    market: str, statistics: MatchStatistics | None, outcome: bool | None, observed_total: int | None = None
) -> str:
    spec = SECONDARY_MARKET_DEFINITIONS.get(market)
    if spec is None:
        return "not_applicable"
    total = observed_total
    if total is None and statistics is not None:
        home, away = statistics.home_corners, statistics.away_corners
        if home is not None and away is not None and min(home, away) >= 0:
            total = home + away
    if total is None or outcome is None:
        return "unverified"
    expected = total > spec.line if spec.kind == "over" else total < spec.line
    return "confirmed" if expected == outcome else "disputed"


def historical_evidence(query):
    """Require a pre-kickoff timestamp and independently check corner outcomes.

    These are timestamp-screened observations. Legacy inputs lack availability
    timestamps, so this sample cannot serve as a verified promotion holdout.
    """
    query = query.outerjoin(MatchStatistics, MatchStatistics.match_id == Match.id)
    total = func.coalesce(
        observed_corner_total(),
        case((
            (MatchStatistics.home_corners >= 0) & (MatchStatistics.away_corners >= 0),
            MatchStatistics.home_corners + MatchStatistics.away_corners,
        )),
    )
    corners = []
    for market, spec in SECONDARY_MARKET_DEFINITIONS.items():
        satisfied = total > spec.line if spec.kind == "over" else total < spec.line
        corners.append((Prediction.market == market) & (PredictionResult.outcome == satisfied))
    return query.filter(
        active_market(),
        stored_pre_kickoff(),
        Match.status == "finished",
        PredictionResult.settled_at >= Match.kickoff_utc,
        PredictionResult.settlement_status.is_distinct_from("void"),
        (~Prediction.market.in_(tuple(SECONDARY_MARKET_DEFINITIONS)))
        | or_(*corners),
    )
