import datetime as dt
from types import SimpleNamespace


def _fixture(db, *, status="finished", home=1, away=2, kickoff=dt.datetime(2031, 3, 4, 7, 45)):
    from backend.app.db.models.core import Competition, Season, Team
    from backend.app.db.models.matches import Match

    league = db.query(Competition).filter_by(code="afc_champions_league").one_or_none()
    if not league:
        league = Competition(code="afc_champions_league", name="AFC Elite", country="Asia")
        db.add(league)
        db.flush()
    season = Season(competition_id=league.id, label=f"archive-test-{kickoff:%d%H%M}{status}")
    teams = [Team(canonical_name=f"Archive team {i} {kickoff:%H%M}{status}") for i in range(2)]
    db.add_all([season, *teams])
    db.flush()
    match = Match(provider="api_football", provider_id=f"api-football-9{kickoff:%d%H%M}{len(status)}", competition_id=league.id,
                  season_id=season.id, kickoff_utc=kickoff, status=status, home_team_id=teams[0].id,
                  away_team_id=teams[1].id, home_goals=home if status == "finished" else None,
                  away_goals=away if status == "finished" else None)
    db.add(match)
    db.flush()
    return match


def test_archived_forecast_is_graded_and_ignored_if_captured_after_kickoff(db_session):
    from backend.app.db.models.publication import PreMatchForecast
    from backend.app.services.forecast_archive import pre_match_forecast

    match = _fixture(db_session)
    payload = {"forecast": {"market": "home_team_over_0_5", "probability": .78, "odds": 1.40},
               "opportunity": None, "markets": {"home_team_over_0_5": {"probability": .78}, "home_win": {"probability": .41}}}
    row = PreMatchForecast(match_id=match.id, captured_at=match.kickoff_utc - dt.timedelta(minutes=20), payload=payload)
    db_session.add(row)
    db_session.flush()
    result = pre_match_forecast(db_session, match)
    assert result["source"] == "previsión_prepartido"
    assert result["forecast"]["is_correct"] is True  # home scored once in a 1–2
    assert {m["market"]: m["is_correct"] for m in result["markets"]} == {"home_team_over_0_5": True, "home_win": False}
    row.captured_at = match.kickoff_utc + dt.timedelta(minutes=1)
    db_session.flush()
    assert pre_match_forecast(db_session, match) is None


def test_laboratory_capture_is_the_fallback(db_session):
    from backend.app.db.models.publication import StrategyCapture
    from backend.app.services.forecast_archive import pre_match_forecast

    match = _fixture(db_session, kickoff=dt.datetime(2031, 3, 4, 10))
    common = [{"market": "over_1_5", "price": 1.35, "probabilities": {"original": .80}},
              {"market": "over_1_5", "price": 1.33, "probabilities": {"original": .79}},
              {"market": "double_chance_1x", "price": 1.10, "probabilities": {"original": .91}}]
    db_session.add(StrategyCapture(match_id=match.id, captured_at=match.kickoff_utc - dt.timedelta(minutes=25),
                                   payload={"common": common}))
    db_session.flush()
    result = pre_match_forecast(db_session, match)
    assert result["source"] == "captura_laboratorio"
    assert result["forecast"]["market"] == "over_1_5" and result["forecast"]["is_correct"] is True
    assert [m["market"] for m in result["markets"]] == ["double_chance_1x", "over_1_5"]


def test_archive_freezes_at_kickoff(db_session, monkeypatch):
    from backend.app.db.models.publication import PreMatchForecast
    from backend.app.services import forecast_archive, today_preview

    now = dt.datetime(2031, 3, 4, 6)
    match = _fixture(db_session, status="scheduled", kickoff=dt.datetime(2031, 3, 4, 7, 45))
    monkeypatch.setattr(today_preview, "build_today_previews",
                        lambda db, fixtures, at: {m.id: {"forecast": {"at": at}} for m in fixtures})
    assert forecast_archive.archive_pre_match_forecasts(db_session, now) == 1
    assert forecast_archive.archive_pre_match_forecasts(db_session, now + dt.timedelta(minutes=30)) == 1
    row = db_session.get(PreMatchForecast, match.id)
    db_session.refresh(row)
    assert row.captured_at == now + dt.timedelta(minutes=30)
    assert forecast_archive.archive_pre_match_forecasts(db_session, match.kickoff_utc + dt.timedelta(minutes=1)) == 0


def test_recent_results_only_write_terminal_states(db_session, monkeypatch):
    from backend.app.services import recent_results

    now = dt.datetime(2031, 3, 4, 12)
    finished = _fixture(db_session, status="live", kickoff=now - dt.timedelta(hours=2))
    playing = _fixture(db_session, status="scheduled", kickoff=now - dt.timedelta(minutes=30))
    replies = {
        finished.provider_id.removeprefix("api-football-"): {"status": {"short": "FT"}, "goals": {"home": 2, "away": 1}},
        playing.provider_id.removeprefix("api-football-"): {"status": {"short": "2H"}, "goals": {"home": 0, "away": 0}},
    }

    class FakeClient:
        def __init__(self, http):
            pass

        def is_available(self):
            return True

        def _get(self, path, params):
            return [{"fixture": {"id": int(i), "status": replies[i]["status"]}, "goals": replies[i]["goals"]}
                    for i in params["ids"].split("-") if i in replies]

    monkeypatch.setattr(recent_results, "ApiFootballClient", FakeClient)
    assert recent_results.refresh_recent_results(db_session, now) == 1
    assert (finished.status, finished.home_goals, finished.away_goals) == ("finished", 2, 1)
    assert playing.status == "scheduled"


def test_archived_picks_follow_the_round_accuracy_rule(db_session):
    from backend.app.db.models.publication import PreMatchForecast, StrategyCapture
    from backend.app.services.forecast_archive import archived_model_picks

    archived = _fixture(db_session, kickoff=dt.datetime(2031, 3, 5, 10))
    captured = _fixture(db_session, kickoff=dt.datetime(2031, 3, 5, 12))
    below = _fixture(db_session, kickoff=dt.datetime(2031, 3, 5, 14))
    before = lambda m: m.kickoff_utc - dt.timedelta(minutes=15)
    db_session.add_all([
        PreMatchForecast(match_id=archived.id, captured_at=before(archived), payload={"markets": {
            "home_win": {"original_probability": .70, "consensus_odds": 1.55},
            "double_chance_1x": {"original_probability": .90, "consensus_odds": 1.12},  # price below the band
            "over_1_5": {"original_probability": .74, "consensus_odds": 1.40},
        }}),
        StrategyCapture(match_id=captured.id, captured_at=before(captured), payload={"common": [
            {"market": "corners_over_9_5", "price": 1.73, "probabilities": {"original": .744}}]}),
        StrategyCapture(match_id=below.id, captured_at=before(below), payload={"common": [
            {"market": "home_win", "price": 1.65, "probabilities": {"original": .619}}]}),
    ])
    db_session.flush()
    picks = archived_model_picks(db_session, [archived, captured, below])
    assert (picks[archived.id].market, picks[archived.id].market_odds) == ("over_1_5", 1.40)
    assert picks[captured.id].market == "corners_over_9_5"
    assert below.id not in picks


def test_candidate_opportunities_list_only_verified_picks_of_on_the_fly_competitions(db_session, monkeypatch):
    from backend.app.api.routes import matches as routes
    from backend.app.db import database
    from backend.app.services import response_cache, today_preview

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    with_pick = _fixture(db_session, status="scheduled", kickoff=now + dt.timedelta(hours=5))
    without = _fixture(db_session, status="scheduled", kickoff=now + dt.timedelta(hours=6))
    pick = {"market": "home_win", "probability": .78, "odds": 1.43, "bookmaker": "B", "ev": .115,
            "quote_age_minutes": 30, "probability_source": "calibrated", "model_version_id": 1}
    monkeypatch.setattr(today_preview, "build_today_previews", lambda db, fixtures, at: {
        m.id: {"opportunity": pick if m.id == with_pick.id else None, "counts": {"eligible": 1, "local_evidence": 0}}
        for m in fixtures})
    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(db_session, "rollback", lambda: None)
    response_cache.clear()
    rows = routes.list_candidate_opportunities(days=1)
    ids = [row["match"]["id"] for row in rows]
    assert with_pick.id in ids and without.id not in ids
    assert next(r for r in rows if r["match"]["id"] == with_pick.id)["opportunity"]["market"] == "home_win"
    response_cache.clear()
