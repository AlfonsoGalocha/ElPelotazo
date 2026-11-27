from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, or_, func, select, union
from sqlalchemy.orm import Session, joinedload

from backend.app.normalization.competitions import CONTINENTAL_CHAMPIONS, EXPERIMENTAL_CATALOG
from backend.app.db.database import get_db
from backend.app.db.models.core import Competition, Season
from backend.app.db.models.features import TeamMatchFeatures
from backend.app.db.models.matches import CornerObservation, Match, MatchOdds, MatchStatistics
from backend.app.db.models.modeling import Prediction, PredictionResult, PredictionSnapshot
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
    ).filter(Match.status == "finished", ~Match.competition.has(Competition.code.in_(EXPERIMENTAL_CATALOG))).one()
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
def round_accuracy(db: Session = Depends(get_db), all_rounds: bool = False):
    """Report one aggregate per round without regenerating historic predictions."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rounds = []
    stake_cents = 1_000
    leagues = db.query(Competition).filter(
        Competition.code.in_(("laliga", "premier_league", "serie_a", "bundesliga", "ligue_1", *CONTINENTAL_CHAMPIONS, "liga_f"))
    ).order_by(Competition.name).all()
    recorded_by_league = {}
    if all_rounds:
        league_ids = [league.id for league in leagues]
        sources = [
            select(Match.competition_id, source.match_id).join(Match, Match.id == source.match_id).where(
                Match.competition_id.in_(league_ids), Match.kickoff_utc <= now,
                source.created_at < Match.kickoff_utc,
            )
            for source in (Prediction, PredictionSnapshot)
        ]
        for competition_id, match_id in db.execute(union(*sources)):
            recorded_by_league.setdefault(competition_id, []).append(match_id)
    groups = []
    for league in leagues:
        champions = league.code in CONTINENTAL_CHAMPIONS
        round_key = Match.round_name if champions else Match.matchday
        eligible = db.query(Match.season_id, Match.matchday, Match.round_name, Match.kickoff_utc).filter(
            Match.competition_id == league.id, round_key.is_not(None),
            Match.kickoff_utc <= now, Match.status.in_(("scheduled", "finished")),
        )
        if all_rounds:
            recorded_ids = recorded_by_league.get(league.id)
            if not recorded_ids:
                continue
            keys = eligible.filter(Match.id.in_(recorded_ids)).with_entities(
                Match.season_id, round_key.label("round_key"), func.max(Match.kickoff_utc)
            ).group_by(Match.season_id, round_key).all()
            keys = [(season_id, key, kickoff) for season_id, key, kickoff in keys]
        else:
            latest = eligible.order_by(Match.kickoff_utc.desc(), Match.id.desc()).first()
            if latest is None:
                continue
            key = latest.round_name if champions else eligible.filter(
                Match.season_id == latest.season_id).order_by(Match.matchday.desc()).first().matchday
            keys = [(latest.season_id, key, latest.kickoff_utc)]
        if not keys:
            continue
        fixtures = db.query(Match, FixturePublication, observed_corner_total()).options(
            joinedload(Match.statistics),
        ).outerjoin(
            FixturePublication, and_(
                FixturePublication.match_id == Match.id,
                FixturePublication.captured_at < Match.kickoff_utc,
            ),
        ).filter(Match.competition_id == league.id, or_(*(
            and_(Match.season_id == season_id, round_key == key) for season_id, key, _ in keys
        ))).all()
        by_round = {}
        for match, publication, corners in fixtures:
            key = match.round_name if champions else match.matchday
            by_round.setdefault((match.season_id, key), []).append((match, publication, corners))
        for season_id, key, kickoff in keys:
            groups.append((league, season_id, None if champions else key, key if champions else None,
                           kickoff, by_round.get((season_id, key), [])))

    seasons = dict(db.query(Season.id, Season.label).filter(Season.id.in_({g[1] for g in groups})))
    model_picks = latest_model_picks(db, list({match.id for *_, fixtures in groups for match, _, _ in fixtures}))
    # Champions, AFC Elite and Liga F forecast on the fly: their pre-match forecast lives in the archive.
    from backend.app.services.forecast_archive import archived_model_picks

    unstored = [match for *_, fixtures in groups for match, _, _ in fixtures if match.id not in model_picks]
    model_picks.update(archived_model_picks(db, unstored))
    for league, season_id, matchday, round_name, kickoff, fixtures in groups:
        counts = dict(wins=0, losses=0, pending=0, without_forecast=0, cancelled=0, awaiting_settlement=0)
        model_counts = dict(wins=0, losses=0, without_forecast=0, awaiting_settlement=0)
        returned_cents = 0
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
                if totals is model_counts and actual is True:
                    returned_cents += int((Decimal(str(pick.market_odds)) * stake_cents).quantize(
                        Decimal(1), rounding=ROUND_HALF_UP,
                    ))
        staked_cents = stake_cents * (model_counts["wins"] + model_counts["losses"])
        rounds.append({
            "competition": league.code, "name": league.name, "matchday": matchday, "round_name": round_name,
            "season": seasons[season_id], "kickoff": kickoff.isoformat() + "Z",
            "fixtures": len(fixtures), "model": model_counts, **counts,
            "flat_stake": {"staked_cents": staked_cents, "returned_cents": returned_cents},
        })

    return rounds


@router.get("/bot-bankroll")
def bot_bankroll(trial: bool = False, live: bool = False, db: Session = Depends(get_db)):
    from backend.app.services.bot_bankroll import bot_report

    return bot_report(db, trial=trial, include_live=live)


@router.get("/bot-plan")
def bot_plan():
    from backend.app.db.database import SessionLocal
    from backend.app.services.bot_bankroll import bot_plan as build_plan
    from backend.app.services.response_cache import cached

    def compute():
        session = SessionLocal()
        try:
            return build_plan(session)
        finally:
            session.rollback()
            session.close()

    plan, age = cached("bot-plan", compute, fresh_seconds=180, max_stale_seconds=900)
    return dict(plan, cache_age_seconds=round(age))


@router.get("/bot-bankroll/simulations")
def bot_bankroll_simulations():
    from backend.app.services.bot_bankroll import risk_simulations

    return risk_simulations()


@router.get("/strategies")
def strategies(
    band: str = "principal", competition: str | None = None,
    scope: str = Query("primary", pattern="^(primary|experimental|all)$"),
    market: str | None = None, strategy: str | None = None,
    date_from: date | None = None, date_to: date | None = None,
    db: Session = Depends(get_db),
):
    from backend.app.services.strategy_shadow import BANDS, WEIGHTS, strategy_report

    if band not in BANDS:
        raise HTTPException(status_code=422, detail="Rango experimental desconocido")
    if strategy is not None and strategy not in WEIGHTS:
        raise HTTPException(status_code=422, detail="Estrategia desconocida")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="Periodo inválido")
    return strategy_report(db, band, competition, scope=scope, market=market, strategy=strategy,
                           date_from=date_from, date_to=date_to)


@router.get("/strategies/operations")
def strategies_operations(db: Session = Depends(get_db)):
    from backend.app.services.strategy_operations import operations_report

    return operations_report(db)


@router.get("/learning")
def model_learning(db: Session = Depends(get_db)):
    from backend.app.services.continuous_learning import comparison_report

    return comparison_report(db)


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
