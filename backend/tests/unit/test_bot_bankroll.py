from types import SimpleNamespace

import pytest

from backend.app.services.bot_bankroll import settle_ticket


@pytest.mark.parametrize(
    ("second_status", "second_home", "outcome"),
    [
        ("scheduled", None, ("pending", 0)),
        ("finished", 0, ("lost", 0)),
        ("finished", 2, ("won", 1440)),
        ("cancelled", None, ("won", 900)),
    ],
)
def test_double_settlement(second_status, second_home, outcome):
    ticket = {
        "stake_cents": 600,
        "legs": [
            {"match_id": 1, "market": "home_win", "price": 1.50},
            {"match_id": 2, "market": "home_win", "price": 1.60},
        ],
    }
    fixtures = {
        1: (SimpleNamespace(status="finished", home_goals=2, away_goals=1), None),
        2: (SimpleNamespace(status=second_status, home_goals=second_home, away_goals=1), None),
    }
    assert settle_ticket(ticket, fixtures) == outcome


def test_corners_await_verified_result():
    ticket = {"stake_cents": 600, "legs": [{"match_id": 1, "market": "corners_over_9_5", "price": 1.50}]}
    match = SimpleNamespace(status="finished", home_goals=2, away_goals=1)
    assert settle_ticket(ticket, {1: (match, None)}) == ("pending", 0)
    assert settle_ticket(ticket, {1: (match, 10)}) == ("won", 900)
    assert settle_ticket(ticket, {1: (match, 9)}) == ("lost", 0)


def _pick(market, p):
    return SimpleNamespace(market=market, id=1), SimpleNamespace(probability=p, score=0.5)


def test_user_market_order_and_scoring_evidence():
    from backend.app.services.bot_match_context import choose_match_pick

    over = _pick("over_2_5", 0.70)
    both = _pick("btts", 0.75)
    cover = _pick("double_chance_1x", 0.83)
    away_cover = _pick("double_chance_x2", 0.82)
    context = {
        "favorite": "home",
        "h2h": {"matches": 4, "both_scored": 3},
        "home_form": {"matches": 5, "scored": 4},
        "away_form": {"matches": 5, "scored": 3},
    }

    assert choose_match_pick([over, both, cover], context) == (over, "goles")
    assert choose_match_pick([both, cover], context) == (both, "ambos_marcan")
    context["away_form"]["scored"] = 2
    assert choose_match_pick([both, cover], context) == (cover, "doble_oportunidad")
    context["favorite"] = "away"
    assert choose_match_pick([both, cover, away_cover], context) == (away_cover, "doble_oportunidad")
    assert choose_match_pick([both, cover], context) == (None, "pasar")


def test_trial_report_keeps_its_own_opening_balance(db_session):
    from datetime import date

    from backend.app.db.models.publication import BotDecision, BotTrialDecision
    from backend.app.services.bot_bankroll import bot_report

    day = date(2026, 11, 23)
    db_session.add(BotDecision(day=day, payload={"day": day.isoformat(), "tickets": []}))
    db_session.flush()
    assert bot_report(db_session, trial=True)["days"] == []
    db_session.add(BotTrialDecision(day=day, payload={"day": day.isoformat(), "tickets": [], "trial": True}))
    db_session.flush()
    report = bot_report(db_session, trial=True)
    assert report["balance_cents"] == 30_000
    assert report["days"][0]["trial"] is True
    assert "trial" not in bot_report(db_session)["days"][0]


def test_continuous_trial_keeps_balance_and_does_not_repeat_a_fixture(db_session, monkeypatch):
    import datetime as dt

    from backend.app.db.models.core import Competition, Season, Team
    from backend.app.db.models.matches import Match
    from backend.app.db.models.publication import BotTrialCut, BotTrialDecision
    from backend.app.services import bot_bankroll
    from backend.app.services import prediction_service

    db = db_session
    league = db.query(Competition).filter_by(code="afc_champions_league").one_or_none()
    if not league:
        league = Competition(code="afc_champions_league", name="AFC Elite", country="Asia")
        db.add(league)
    home, away = Team(canonical_name="Rolling Trial Home"), Team(canonical_name="Rolling Trial Away")
    db.add_all([home, away])
    db.flush()
    season = Season(competition_id=league.id, label="rolling-trial")
    db.add(season)
    db.flush()
    now = dt.datetime(2026, 11, 24, 8)
    fixtures = []
    for index, (hours, status) in enumerate(((-16, "finished"), (.5, "scheduled"), (3, "scheduled"))):
        match = Match(provider="test", provider_id=f"rolling-trial-{index}", competition_id=league.id,
                      season_id=season.id, kickoff_utc=now + dt.timedelta(hours=hours), status=status,
                      home_team_id=home.id, away_team_id=away.id,
                      home_goals=0 if index == 0 else None, away_goals=1 if index == 0 else None)
        db.add(match)
        fixtures.append(match)
    db.flush()
    db.add(BotTrialDecision(day=dt.date(2026, 11, 23), payload={
        "day": "2026-11-23", "captured_at": "2026-11-23T12:00:00Z",
        "tickets": [{"stake_cents": 8850, "legs": [{"match_id": fixtures[0].id, "market": "home_win", "price": 1.5}]}],
    }))
    db.flush()
    monkeypatch.setattr(prediction_service, "score_api_candidate", lambda *args, **kwargs: (None, [], {}))
    monkeypatch.setattr(bot_bankroll, "market_track_records_with_provenance", lambda *args, **kwargs: {})
    monkeypatch.setattr(bot_bankroll, "match_context", lambda *args: {})
    monkeypatch.setattr(bot_bankroll, "choose_match_pick", lambda *args: (None, "pasar"))

    first = bot_bankroll.capture_bot_decision(db, now=now, trial=True, continuous=True)
    assert first["available_at_cut_cents"] == 21150
    assert [row["match_id"] for row in first["fixtures"]] == [fixtures[1].id]
    assert bot_bankroll.capture_bot_decision(db, now=now, trial=True, continuous=True) is None
    later = bot_bankroll.capture_bot_decision(db, now=now + dt.timedelta(hours=2, minutes=30),
                                               trial=True, continuous=True)
    assert later["available_at_cut_cents"] == 21150
    assert [row["match_id"] for row in later["fixtures"]] == [fixtures[2].id]
    assert db.query(BotTrialCut).filter(BotTrialCut.match_id.in_([fixtures[1].id, fixtures[2].id])).count() == 2


def test_outsider_to_score_uses_same_team_and_inclusive_price_band():
    from backend.app.services.bot_match_context import choose_match_pick

    form = {"matches": 0, "scored": 0}
    context = {"favorite": "away", "h2h": {"matches": 0}, "home_form": form, "away_form": form,
               "team_win_prices": {"home": 4.0, "away": 2.0}}
    for price in (1.39, 1.40, 1.50, 1.51):
        home_pick = _pick("home_team_over_0_5", .76)
        home_pick[1].quote = SimpleNamespace(price=price)
        away_pick = _pick("away_team_over_0_5", .85)
        away_pick[1].quote = SimpleNamespace(price=1.45)
        compared = choose_match_pick([home_pick, away_pick], context)
        assert compared == ((home_pick, "equipo_no_favorito_marca") if 1.40 <= price <= 1.50 else (None, "pasar"))
    context["team_win_prices"]["home"] = 3.99
    home_pick[1].quote.price = 1.45
    assert choose_match_pick([home_pick], context) == (None, "pasar")


def test_live_team_goal_tracks_correct_side_and_var_without_settling():
    from backend.app.services.bot_bankroll import with_live_score

    match = SimpleNamespace(provider="api_football", provider_id="api-football-123", status="scheduled")
    home = {"match_id": 1, "market": "home_team_over_0_5", "price": 1.4}
    away = dict(home, market="away_team_over_0_5")
    score = {"home": 1, "away": 0, "minute": 36, "status": "1H"}
    assert with_live_score(home, match, {"api-football-123": score})["live"]["goal_met"] is True
    assert with_live_score(away, match, {"api-football-123": score})["live"]["goal_met"] is False
    assert with_live_score(home, match, {"api-football-123": dict(score, home=0)})["live"]["goal_met"] is False
    assert with_live_score(home, match, {"api-football-999": score}) == home
    assert "live" not in home
    assert settle_ticket({"legs": [home], "stake_cents": 1000}, {1: (match, None)}) == ("pending", 0)


def test_live_settlement_pays_team_goal_and_resolves_miss_at_full_time():
    match = SimpleNamespace(provider="api_football", provider_id="api-football-123", status="live")
    leg = {"match_id": 1, "market": "home_team_over_0_5", "price": 1.4}
    ticket = {"legs": [leg], "stake_cents": 1000}
    fixtures = {1: (match, None)}
    scores = {"api-football-123": {"home": 1, "away": 0, "status": "2H"}}
    assert settle_ticket(ticket, fixtures, scores) == ("won", 1400)
    scores["api-football-123"]["home"] = 0
    assert settle_ticket(ticket, fixtures, scores) == ("pending", 0)
    scores["api-football-123"]["status"] = "FT"
    assert settle_ticket(ticket, fixtures, scores) == ("lost", 0)
