from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from backend.app.db.models.core import Competition, Season, Team
from backend.app.db.models.matches import Match, MatchOdds, MatchOddsHistory
from backend.app.db.models.modeling import ModelVersion, Prediction
from backend.app.db.models.publication import StrategyCapture
from backend.app.models.calibration.coherent import RESULT, corrected_forecasts, fit_temperature
from backend.app.prediction.opportunities import quote_age
from backend.app.services.strategy_shadow import (
    capture_strategy_due, choose, independent_reference, strategy_report,
)


def test_coherent_calibration_with_legacy_fallback():
    distribution = dict(zip(RESULT, (1., 0., 0.)))
    distribution.update(double_chance_1x=.9, double_chance_x2=.1, double_chance_12=.9,
                        over_2_5=.6, under_2_5=.4, btts=.7,
                        corners_over_9_5=.6, corners_under_9_5=.4)
    legacy = dict(zip(RESULT, (1., .3, .2)))
    legacy.update(over_2_5=.7, under_2_5=.6, btts=1., corners_over_9_5=.55, corners_under_9_5=.8)
    corrected = corrected_forecasts(distribution, legacy)
    p = {name: pair[0] for name, pair in corrected.items()}
    assert all(0 < probability < 1 for probability in p.values())
    assert sum(p[m] for m in RESULT) == pytest.approx(1)
    assert corrected["double_chance_1x"][1] == "original_derived_1x2"
    assert p["double_chance_1x"] == pytest.approx(p["home_win"] + p["draw"])
    assert p["double_chance_x2"] == pytest.approx(p["draw"] + p["away_win"])
    assert p["double_chance_12"] == pytest.approx(p["home_win"] + p["away_win"])
    assert p["over_2_5"] + p["under_2_5"] == pytest.approx(1)
    assert p["corners_over_9_5"] + p["corners_under_9_5"] == pytest.approx(1)
    assert corrected["btts"] == (.7, "original_fallback")


def test_temperature_requires_temporal_evidence():
    dates = np.arange(120)
    top = dates % 3
    labels = (top + (dates % 4 == 0)) % 3
    p = np.full((len(dates), 3), .01)
    p[dates, top] = .98
    assert fit_temperature(p[:10], labels[:10], dates[:10])[0] is None
    t, report = fit_temperature(p[::-1], labels[::-1], dates[::-1])
    assert t > 1 and report["reason"] == "accepted"
    assert report["validation_start"] == "84"
    rng = np.random.default_rng(42)
    flat = np.full((120, 3), 1/3)
    assert fit_temperature(flat, rng.integers(0, 3, 120), dates)[0] is None


def test_independent_reference_requires_two_complete_contemporaneous_houses():
    now = datetime(2026, 11, 23, 18)
    offer = SimpleNamespace(bookmaker="offer", provider_updated_at=now)
    rows = [SimpleNamespace(bookmaker=house, market="match_result", line=None, selection=side,
                            price=price, history_quote_id=i, provider_updated_at=now)
            for i, (house, side, price) in enumerate(
                (h,s,p) for h in ("offer", "other1", "other2") for s,p in (("home",2),("draw",3),("away",4)))]
    lookup = {"home_win": ("match_result", None, "home")}
    ref = independent_reference(rows, "home_win", lookup, offer)
    assert ref["houses"] == 2 and ref["quote_ids"] == list(range(3, 9))
    assert ref["probability"] == pytest.approx(.5 / (.5 + 1/3 + .25))
    assert independent_reference(rows[:-1], "home_win", lookup, offer) is None
    assert quote_age(SimpleNamespace(bookmaker="", archived_quote=rows[0]), now) is None


def test_chooses_one_with_positive_ev_and_fixed_band():
    base = {"probabilities": {"original": .72}, "scores": {"original": .6}, "price": 1.5, "quote_id": 1}
    better = {**base, "scores": {"original": .7}, "quote_id": 2}
    negative = {**better, "price": 1.36, "scores": {"original": .9}}
    assert choose([base, better, negative], "original", "principal", .65)["quote_id"] == 2
    assert choose([negative], "original", "principal", .65) is None
    assert choose([base], "original", "1.20-1.34", .65) is None


@pytest.mark.parametrize("competition", ["laliga", "champions_league"])
def test_capture_and_settle_without_rewriting_snapshot(db_session, monkeypatch, competition):
    db = db_session
    now = datetime(2026, 11, 23, 18)
    league = Competition(code=competition, name=competition, country="Spain")
    home, away = Team(canonical_name="Home"), Team(canonical_name="Away")
    db.add_all([league, home, away])
    db.flush()
    season = Season(competition_id=league.id, label="2026/27")
    version = ModelVersion(name="legacy", version="x", market_family="goals", features=[],
                           hyperparameters={}, metrics={}, dataset_version="test",
                           training_end=now.date()+timedelta(days=1), created_at=now-timedelta(days=1))
    db.add_all([season, version])
    db.flush()
    match = Match(provider="test", provider_id="test", competition_id=league.id, season_id=season.id,
                  kickoff_utc=now+timedelta(minutes=30), home_team_id=home.id, away_team_id=away.id,
                  matchday=13, status="scheduled")
    db.add(match)
    db.flush()
    for market, probability, side in zip(RESULT, (.80,.13,.07), ("home", "draw", "away")):
        db.add(Prediction(match_id=match.id, market=market, selection=side,
                          model_version_id=version.id, created_at=now-timedelta(minutes=1),
                          model_probability=probability, calibrated_probability=.91,
                          fair_odds=1/probability, confidence=.8, data_quality=.8, explanation={}, features_used={}))
    db.add(Prediction(match_id=match.id, market="home_win", selection="home",
                      model_version_id=version.id, created_at=now+timedelta(minutes=2),
                      model_probability=.99, fair_odds=1.01, confidence=.8, data_quality=.8,
                      explanation={}, features_used={}))
    for house in ("Offer", "Other 1", "Other 2"):
        for side, price in (("home",1.5),("draw",4),("away",8)):
            data = dict(match_id=match.id, bookmaker=house, market="match_result", selection=side,
                        price=price, snapshot_type="pre_match", provider_updated_at=now-timedelta(minutes=5),
                        fetched_at=now-timedelta(minutes=4), recorded_at=now-timedelta(minutes=3))
            archived = MatchOddsHistory(**data)
            db.add(archived)
            db.flush()
            db.add(MatchOdds(**data, history_quote_id=archived.id))
    db.flush()
    from backend.app.services.promotion_service import evaluate_and_promote

    assert evaluate_and_promote(db, league.code, version)["reason"] == "uncertified_training_cutoff"
    if competition == "champions_league":
        from backend.app.services import prediction_service

        db.query(Prediction).filter(Prediction.match_id == match.id).delete()
        rows = [SimpleNamespace(
            match_id=match.id, market=market, model_probability=p, calibrated_probability=p,
            data_quality=.8, confidence=.8, uncertainty_low=.6, uncertainty_high=.9, calibration_source="joint_temperature",
        ) for market, p in zip(RESULT, (.80, .13, .07))]
        monkeypatch.setattr(prediction_service, "score_champions_candidate", lambda *_: (version, rows, {}))
    assert capture_strategy_due(db, now) == 1
    capture = db.get(StrategyCapture, match.id)
    data = capture.payload
    assert data["decisions"]["original:principal"]["probability"] == pytest.approx(.8)
    corrected = data["decisions"]["corregida:principal"]
    if competition == "champions_league":
        assert corrected["prediction_source"] == "champions_candidate"
        assert corrected["prediction_id"] is None
        assert corrected["corrected_source"] == "joint_temperature"
        assert db.query(Prediction).filter_by(match_id=match.id).count() == 0
    else:
        assert corrected["corrected_source"] == "original_simplex_fallback"
    assert capture_strategy_due(db, now+timedelta(minutes=5)) == 0
    assert strategy_report(db)["strategies"]["original"]["settled"] == 0
    match.status, match.home_goals, match.away_goals = "finished", 2, 0
    db.flush()
    report = strategy_report(db)
    assert report["mode"] == "prospective_shadow"
    assert report["strategies"]["original"]["profit_cents"] == 500
    assert report["strategies"]["original"]["roi"] == .5
    assert report["strategies"]["mercado"]["common_quality"]["n"] == 1
    assert report["history"][0]["decisions"]["original"]["outcome"] == "won"
    assert db.get(StrategyCapture, match.id).payload == data


def test_missed_capture_labels_only_what_the_scan_proves():
    from backend.app.services.strategy_operations import FIRST_SHADOW_DEPLOYMENT, missed_reason

    assert missed_reason(FIRST_SHADOW_DEPLOYMENT-timedelta(hours=1), ()) == "before_deployment"
    kickoff = FIRST_SHADOW_DEPLOYMENT+timedelta(days=1)
    assert missed_reason(kickoff, ()) == "no_recorded_scan"
    assert missed_reason(kickoff, [SimpleNamespace(ok=False)]) == "scan_failed"
    assert missed_reason(kickoff, [SimpleNamespace(ok=True)]) == "unknown"


def test_retries_missing_inputs_until_final_part_of_window(db_session):
    now = datetime(2026, 11, 23, 18)
    league = Competition(code="laliga", name="La Liga", country="Spain")
    home, away = Team(canonical_name="Home"), Team(canonical_name="Away")
    db_session.add_all([league, home, away])
    db_session.flush()
    season = Season(competition_id=league.id, label="2026/27")
    db_session.add(season)
    db_session.flush()
    match = Match(provider="test", provider_id="test-no-data", competition_id=league.id, season_id=season.id,
                  kickoff_utc=now+timedelta(minutes=30), home_team_id=home.id, away_team_id=away.id,
                  matchday=13, status="scheduled")
    db_session.add(match)
    db_session.flush()
    assert capture_strategy_due(db_session, now) == 0
    assert capture_strategy_due(db_session, now+timedelta(minutes=20)) == 1
    capture = db_session.get(StrategyCapture, match.id)
    assert capture.payload["reason"] == "no_prediction"
    assert len(capture.payload["decisions"]) == 48
    assert all(value is None for value in capture.payload["decisions"].values())
    assert capture_strategy_due(db_session, now+timedelta(minutes=31)) == 0
