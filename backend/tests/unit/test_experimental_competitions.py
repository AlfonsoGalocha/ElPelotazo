import datetime as dt

import pytest

from backend.app.api.routes.matches import list_matches
from backend.app.api.routes.predictions import _base_signal_query
from backend.app.db.models.core import Season, Team
from backend.app.db.models.matches import Match, MatchOddsHistory
from backend.app.db.models.modeling import ModelVersion, Prediction
from backend.app.db.models.publication import BotManualTicket, BotTrialCut, StrategyCapture
from backend.app.normalization.competitions import PRIMARY_COMPETITIONS, resolve_competition_id
from backend.app.services import bot_bankroll
from backend.app.services.promotion_service import evaluate_and_promote
from backend.app.services.strategy_shadow import WEIGHTS, capture_strategy_due, strategy_report


def test_experimental_fixture_is_visible_only_in_its_scope_and_never_in_official_decisions(db_session):
    db = db_session
    now = dt.datetime.utcnow()
    assert len(PRIMARY_COMPETITIONS) == 8
    competition_id = resolve_competition_id(db, "championship")
    teams = [Team(canonical_name=f"Research isolation {side}") for side in ("home", "away")]
    season = Season(competition_id=competition_id, label="2099/00")
    version = ModelVersion(name="research", version="r1", market_family="goals", features=[],
                           hyperparameters={}, metrics={}, dataset_version="championship:1_matches", is_active=False)
    db.add_all([*teams, season, version])
    db.flush()
    match = Match(provider="test", provider_id="experimental-isolation", competition_id=competition_id,
                  season_id=season.id, kickoff_utc=now + dt.timedelta(minutes=10), home_team_id=teams[0].id,
                  away_team_id=teams[1].id, status="scheduled")
    db.add(match)
    db.flush()
    db.add(Prediction(match_id=match.id, market="over_2_5", line=2.5, selection="over",
                      model_version_id=version.id, created_at=now - dt.timedelta(minutes=1), model_probability=.7, calibrated_probability=.68,
                      fair_odds=1/.7, confidence=.8, data_quality=.8, explanation={}, features_used={}))
    quote = MatchOddsHistory(match_id=match.id, market="over_under_goals", line=2.5, selection="over",
                             bookmaker="test", price=1.7, snapshot_type="pre_match", recorded_at=now,
                             fetched_at=now, provider_updated_at=now)
    db.add(quote)
    db.flush()

    def listing(scope):
        return list_matches(db=db, competition_scope=scope, date_from=None, date_to=None, search=None, status=None, limit=999)

    assert match in listing("experimental") and match in listing("all")
    assert match not in listing("primary")
    assert _base_signal_query(db, None, True, scope="all_upcoming", competition_code="championship").count() == 0
    assert match not in db.query(Match).join(Match.competition).filter(bot_bankroll.trial_competitions()).all()
    with pytest.raises(ValueError, match="competiciones principales"):
        bot_bankroll.record_manual_ticket(db, quote.id, 100)
    assert not evaluate_and_promote(db, "championship", version)["promoted"]

    assert capture_strategy_due(db, now) == 0
    assert capture_strategy_due(db, now, experimental=True) == 1
    saved = db.get(StrategyCapture, match.id)
    assert saved.captured_at < match.kickoff_utc
    assert saved.payload["reason"] == "no_valid_quotes"
    assert len(saved.payload["decisions"]) == len(WEIGHTS) * len(saved.payload["bands"])
    assert "bot_observation" in saved.payload
    assert capture_strategy_due(db, now, experimental=True) == 0
    assert strategy_report(db)["fixtures"] == 0
    assert strategy_report(db, scope="experimental")["fixtures"] == 1
    assert db.query(BotTrialCut).count() == 0 and db.query(BotManualTicket).count() == 0
