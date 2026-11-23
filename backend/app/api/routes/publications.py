from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from backend.app.db.database import get_db
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.features import TeamMatchFeatures
from backend.app.db.models.matches import CornerObservation, Match, MatchOdds, MatchStatistics
from backend.app.db.models.modeling import Prediction, PredictionResult
from backend.app.db.models.publication import FixturePublication, FixtureRankingRevision
from backend.app.prediction.eligibility import observed_corner_total
from backend.app.services.publication_service import latest_model_picks, publication_report, selection_outcome

router = APIRouter(prefix="/publications", tags=["publications"])


@router.get("")
def publications(
    competition: str | None = None,
    market: str | None = None,
    model_version: int | None = None,
    min_probability: float | None = Query(None, ge=0, le=1),
    matchday: int | None = None,
    outcome: str | None = None,
    max_probability: float | None = Query(None, ge=0, le=1),
    db: Session = Depends(get_db),
):
    return publication_report(
        db, competition, market, model_version, min_probability, matchday, outcome, max_probability
    )


@router.get("/coverage")
def publication_coverage(db: Session = Depends(get_db)):
    total, before = db.query(
        func.count(PredictionResult.id),
        func.count(PredictionResult.id).filter(Prediction.created_at < Match.kickoff_utc),
    ).join(Prediction, Prediction.id == PredictionResult.prediction_id).join(
        Match, Match.id == Prediction.match_id
    ).filter(Match.status == "finished").one()
    official = db.query(FixturePublication).join(Match, Match.id == FixturePublication.match_id).filter(
        FixturePublication.captured_at < Match.kickoff_utc
    )
    today = date.today()
    current_season_start = today.year - (today.month < 7)
    seasons = [f"{year}/{(year + 1) % 100:02d}" for year in range(current_season_start - 6, current_season_start)]
    verified_corners = (
        (MatchStatistics.home_corners >= 0) & (MatchStatistics.away_corners >= 0)
    ) | db.query(CornerObservation.id).filter(CornerObservation.match_id == Match.id).exists()
    closing_prices = db.query(
        MatchOdds.match_id,
        func.count().filter(MatchOdds.market == "match_result").label("result_prices"),
        func.count().filter(
            (MatchOdds.market == "over_under_goals") & (MatchOdds.line == 2.5)
        ).label("goals_prices"),
        func.count().filter(MatchOdds.market == "corners_total").label("corner_prices"),
    ).filter(
        MatchOdds.snapshot_type == "closing", MatchOdds.price > 1
    ).group_by(MatchOdds.match_id).subquery()
    completed, corners, closing, result_prices, goals_prices, corner_prices = db.query(
        func.count(Match.id), func.count(Match.id).filter(verified_corners),
        func.count(closing_prices.c.match_id),
        func.count().filter(closing_prices.c.result_prices > 0),
        func.count().filter(closing_prices.c.goals_prices > 0),
        func.count().filter(closing_prices.c.corner_prices > 0),
    ).select_from(Match).join(Season, Season.id == Match.season_id).join(
        Competition, Competition.id == Match.competition_id
    ).outerjoin(MatchStatistics, MatchStatistics.match_id == Match.id).outerjoin(
        closing_prices, closing_prices.c.match_id == Match.id
    ).filter(
        Competition.code.in_(("laliga", "bundesliga", "premier_league", "serie_a", "ligue_1")),
        Season.label.in_(seasons), Match.status == "finished",
        Match.home_goals.is_not(None), Match.away_goals.is_not(None),
    ).one()
    return {
        "training_corpus": {
            "first_season": seasons[0], "last_season": seasons[-1],
            "finished_matches": completed, "matches_with_corners": corners,
            "matches_with_closing_prices": closing,
            "matches_with_closing_result": result_prices,
            "matches_with_closing_goals_2_5": goals_prices,
            "matches_with_closing_corners": corner_prices,
        },
        "historical_liquidated": total,
        "historical_before_kickoff": before,
        "historical_after_kickoff": total - before,
        "official_captures": official.count(),
        "official_finished": official.filter(Match.status == "finished").count(),
    }


@router.get("/round-accuracy")
def round_accuracy(db: Session = Depends(get_db)):
    """Evaluate original model forecasts and official recommendations separately."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rounds = []
    leagues = db.query(Competition).filter(
        Competition.code.in_(("laliga", "premier_league", "serie_a", "bundesliga", "ligue_1"))
    ).order_by(Competition.name).all()
    for league in leagues:
        eligible = db.query(Match.season_id, Match.matchday).filter(
            Match.competition_id == league.id, Match.matchday.is_not(None),
            Match.kickoff_utc <= now, Match.status.in_(("scheduled", "finished")),
        )
        latest = eligible.order_by(Match.kickoff_utc.desc(), Match.id.desc()).first()
        if latest is None:
            continue
        matchday = eligible.filter(Match.season_id == latest.season_id).order_by(Match.matchday.desc()).first().matchday
        fixtures = db.query(Match, FixturePublication, observed_corner_total()).outerjoin(
            FixturePublication, and_(
                FixturePublication.match_id == Match.id,
                FixturePublication.captured_at < Match.kickoff_utc,
            ),
        ).filter(
            Match.competition_id == league.id,
            Match.season_id == latest.season_id,
            Match.matchday == matchday,
        ).all()
        model_picks = latest_model_picks(db, [match.id for match, _, _ in fixtures])

        counts = dict(wins=0, losses=0, pending=0, without_forecast=0, cancelled=0, awaiting_settlement=0)
        model_counts = dict(wins=0, losses=0, without_forecast=0, awaiting_settlement=0)
        for match, publication, corners in fixtures:
            if match.status == "cancelled":
                counts["cancelled"] += 1
                continue
            if match.status != "finished":
                counts["pending"] += 1
                continue
            stats = match.statistics
            if corners is None and stats and stats.home_corners is not None and stats.away_corners is not None:
                if min(stats.home_corners, stats.away_corners) >= 0:
                    corners = stats.home_corners + stats.away_corners
            pick = model_picks.get(match.id)
            recommendation = publication.recommendation if publication else None
            for totals, market in (
                (model_counts, pick.market if pick else None),
                (counts, recommendation["market"] if recommendation else None),
            ):
                if market is None:
                    totals["without_forecast"] += 1
                    continue
                actual = selection_outcome(market, match.home_goals, match.away_goals, corners)
                totals["wins" if actual is True else "losses" if actual is False else "awaiting_settlement"] += 1
        rounds.append({
            "competition": league.code, "name": league.name, "matchday": matchday,
            "fixtures": len(fixtures), "model": model_counts, **counts,
        })

    return rounds


@router.get("/bot-bankroll")
def bot_bankroll(db: Session = Depends(get_db)):
    from backend.app.services.bot_bankroll import bot_report

    return bot_report(db)


@router.get("/{match_id}")
def publication_detail(match_id: int, db: Session = Depends(get_db)):
    publication = db.get(FixturePublication, match_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="No existe una captura oficial para este partido")
    versions = {
        row["feature_set_version"]
        for group in publication.groups.values()
        for row in group["alternatives"]
        if row.get("feature_set_version")
    }
    snapshots = db.query(TeamMatchFeatures).filter(
        TeamMatchFeatures.match_id == match_id,
        TeamMatchFeatures.feature_set_version.in_(versions),
    ).all()
    return {**jsonable_encoder(publication), "feature_snapshots": snapshots}


@router.get("/{match_id}/revisions")
def ranking_revisions(match_id: int, limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db)):
    return db.query(FixtureRankingRevision).filter_by(match_id=match_id).order_by(
        FixtureRankingRevision.captured_at.desc()
    ).limit(limit).all()
