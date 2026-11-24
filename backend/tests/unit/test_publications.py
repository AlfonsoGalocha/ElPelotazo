import datetime as dt

import pytest

from backend.app.api.routes.publications import publication_detail
from backend.app.db.models.core import Competition, Season, Team
from backend.app.db.models.matches import Match, MatchOdds, MatchOddsHistory
from backend.app.db.models.modeling import ModelVersion, Prediction
from backend.app.db.models.publication import FixtureRankingRevision
from backend.app.services.publication_service import capture_publications, publication_report, selection_outcome


@pytest.mark.parametrize(("verified", "terminal_status"), [(False, "finished"), (True, "finished"), (True, "cancelled")])
def test_draw_evaluates_probabilities_but_does_not_win_the_principal_forecast(db_session, verified, terminal_status):
    db = db_session
    league = Competition(code="publication-test", name="Test", country="Test")
    home, away = Team(canonical_name="Real Madrid (test)"), Team(canonical_name="Atlético (test)")
    db.add_all([league, home, away])
    db.flush()
    season = Season(competition_id=league.id, label="2026/27")
    version = ModelVersion(name="test", version="test", market_family="goals", features=[],
                           hyperparameters={}, metrics={}, dataset_version="publication-test")
    db.add_all([season, version])
    db.flush()
    kickoff = dt.datetime.utcnow() - dt.timedelta(hours=2)
    match = Match(provider="test", provider_id="test-1x2", competition_id=league.id,
                  season_id=season.id, kickoff_utc=kickoff, home_team_id=home.id,
                  away_team_id=away.id, status="scheduled")
    db.add(match)
    db.flush()
    predictions = []
    for market, probability, price in zip(
        ("home_win", "draw", "away_win"), (.65, .20, .15), (1.55, 5, 6.5)
    ):
        prediction = Prediction(
            match_id=match.id, selection=market, market=market, model_version_id=version.id,
            created_at=kickoff-dt.timedelta(minutes=45), model_probability=probability,
            market_odds=price, market_probability=.5, fair_odds=1/probability,
            confidence=.7, data_quality=.8, explanation={}, features_used={},
            calibrated_probability=probability, uncertainty_low=probability,
            uncertainty_high=min(1, probability + .06), bookmakers_used=8,
            odds_origin_at=kickoff - dt.timedelta(minutes=50) if verified else None,
        )
        predictions.append(prediction)
        db.add(prediction)
    if verified:
        data = dict(match_id=match.id, bookmaker="Book", market="match_result", line=None,
                    selection="home", price=1.60, snapshot_type="pre_match",
                    provider_updated_at=kickoff-dt.timedelta(minutes=50),
                    fetched_at=kickoff-dt.timedelta(minutes=49), recorded_at=kickoff-dt.timedelta(minutes=48))
        archived = MatchOddsHistory(**data)
        db.add(archived)
        db.flush()
        quote = MatchOdds(**data, history_quote_id=archived.id)
        db.add(quote)
        db.flush()
    capture_publications(db, predictions, {(league.code, "home_win"): .8}, timestamp=kickoff-dt.timedelta(minutes=30))
    predictions[0].model_probability = .10
    if verified:
        quote.price = 1.85
    capture_publications(db, predictions, {(league.code, "home_win"): .8}, timestamp=kickoff-dt.timedelta(minutes=10))
    capture_publications(db, predictions, {(league.code, "home_win"): .8}, timestamp=kickoff)
    assert db.query(FixtureRankingRevision).filter_by(match_id=match.id).count() == 2
    match.status = terminal_status
    match.home_goals = match.away_goals = 1
    db.flush()
    assert publication_detail(match.id, db)["groups"]["1X2"]["principal"] == "home_win"
    report = publication_report(db, league.code)
    assert report["history"][0]["market"] == "home_win"
    assert report["history"][0]["probability"] == .65
    assert report["history"][0]["status"] == ("void" if terminal_status == "cancelled" else "lost")
    if terminal_status == "finished":
        assert report["metrics"][0]["wins"] == 0
        assert report["metrics"][0]["brier"] == pytest.approx(.65**2 + (.20-1)**2 + .15**2)
    else:
        assert report["metrics"] == []
    if verified:
        assert len(report["recommendations"]) == 1
        assert report["recommendations"][0]["market"] == "home_win"
        assert report["recommendations"][0]["odds"] == 1.60
        assert report["recommendations"][0]["bookmaker"] == "Book"
        assert report["recommendations"][0]["quote_history_id"] == archived.id
        if terminal_status == "finished":
            assert report["performance"]["profit_units"] == -1
            assert report["performance"]["roi"] == -1
        else:
            assert report["performance"]["void"] == 1
            assert report["performance"]["roi"] is None
    else:
        assert report["performance"]["profit_units"] == 0
        assert report["performance"]["roi"] is None
        assert report["recommendations"] == []


def test_corners_require_a_verified_count_and_respect_the_line():
    assert selection_outcome("corners_under_9_5", 1, 1, None) is None
    assert selection_outcome("corners_over_8_5", 1, 1, 9)
    assert not selection_outcome("corners_over_9_5", 1, 1, 9)
    assert selection_outcome("corners_under_9_5", 1, 1, 9)


def test_historical_round_accuracy_uses_only_saved_pre_match_forecasts(db_session):
    from backend.app.api.routes.publications import round_accuracy

    db = db_session
    league = db.query(Competition).filter_by(code="laliga").one_or_none()
    if league is None:
        league = Competition(code="laliga", name="La Liga", country="Spain")
        db.add(league)
    home, away = Team(canonical_name="Rounds test home"), Team(canonical_name="Rounds test away")
    version = ModelVersion(name="rounds-test", version="rounds-test", market_family="goals",
                           features=[], hyperparameters={}, metrics={}, dataset_version="rounds-test")
    db.add_all([home, away, version])
    db.flush()
    season = Season(competition_id=league.id, label="rounds-test")
    db.add(season)
    db.flush()
    kickoff = dt.datetime.utcnow() - dt.timedelta(days=3)
    for round_number, delta in ((1, -30), (2, -30), (3, 30)):
        match = Match(provider="test", provider_id=f"rounds-{round_number}", competition_id=league.id,
                      season_id=season.id, kickoff_utc=kickoff + dt.timedelta(days=round_number - 1),
                      matchday=round_number, home_team_id=home.id, away_team_id=away.id,
                      home_goals=2 if round_number == 1 else 0, away_goals=1, status="finished")
        db.add(match)
        db.flush()
        db.add(Prediction(match_id=match.id, market="home_win", selection="home",
                          model_version_id=version.id, created_at=match.kickoff_utc + dt.timedelta(minutes=delta),
                          model_probability=.7, market_odds=1.5, fair_odds=1/.7,
                          confidence=.7, data_quality=.8, explanation={}, features_used={}))
    db.flush()
    rounds = {row["matchday"]: row for row in round_accuracy(db, all_rounds=True) if row["season"] == "rounds-test"}
    assert set(rounds) == {1, 2}
    assert rounds[1]["model"]["wins"] == 1
    assert rounds[2]["model"]["losses"] == 1
    assert sum(row["flat_stake"]["staked_cents"] for row in rounds.values()) == 2000
    assert sum(row["flat_stake"]["returned_cents"] for row in rounds.values()) == 1500
