import datetime as dt

import pytest

from backend.app.api.routes.publications import publication_detail
from backend.app.db.models.core import Competition, Season, Team
from backend.app.db.models.matches import Match
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
    capture_publications(db, predictions, {(league.code, "home_win"): .8}, timestamp=kickoff-dt.timedelta(minutes=30))
    predictions[0].model_probability = .10
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
