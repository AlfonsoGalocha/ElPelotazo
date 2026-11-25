import datetime as dt

import pytest

from backend.app.services import match_analysis as ma

BASE = dt.datetime(2031, 6, 1, 18)


@pytest.fixture()
def world(db_session):
    """Two teams with ten past meetings/fixtures, an analysed fixture and a later one."""
    from backend.app.db.models.core import Competition, Season, Team
    from backend.app.db.models.matches import Match, MatchStatistics

    db = db_session
    league = Competition(code="analysis_league", name="Analysis League", country="X")
    cup = Competition(code="analysis_cup", name="Analysis Cup", country="X")
    db.add_all([league, cup])
    db.flush()
    season = Season(competition_id=league.id, label="analysis-31")
    cup_season = Season(competition_id=cup.id, label="analysis-cup-31")
    teams = [Team(canonical_name=f"Analysis {name}") for name in ("Home", "Away", "Other")]
    db.add_all([season, cup_season, *teams])
    db.flush()
    home, away, other = teams
    rows = []
    # Home team: 12 finished matches, alternating venue; the last two in the cup; two lack statistics.
    for i in range(12):
        is_home = i % 2 == 0
        comp, seas = (cup, cup_season) if i >= 10 else (league, season)
        opponent = away if i in (0, 1) else other
        m = Match(provider="test", provider_id=f"analysis-{i}", competition_id=comp.id, season_id=seas.id,
                  kickoff_utc=BASE - dt.timedelta(days=7 * (i + 1)), status="finished",
                  home_team_id=home.id if is_home else opponent.id, away_team_id=opponent.id if is_home else home.id,
                  home_goals=i % 4, away_goals=1, home_goals_ht=0, away_goals_ht=0)
        db.add(m)
        db.flush()
        if i not in (3, 4):
            db.add(MatchStatistics(match_id=m.id, home_corners=5 + i % 3, away_corners=4, home_shots=12,
                                   away_shots=9, home_shots_on_target=5, away_shots_on_target=3))
        rows.append(m)
    analysed = Match(provider="test", provider_id="analysis-target", competition_id=league.id, season_id=season.id,
                     kickoff_utc=BASE, status="finished", home_team_id=home.id, away_team_id=away.id,
                     home_goals=9, away_goals=9)
    later = Match(provider="test", provider_id="analysis-later", competition_id=league.id, season_id=season.id,
                  kickoff_utc=BASE + dt.timedelta(days=3), status="finished", home_team_id=home.id,
                  away_team_id=other.id, home_goals=7, away_goals=0)
    db.add_all([analysed, later])
    db.flush()
    db.add(MatchStatistics(match_id=analysed.id, home_corners=30, away_corners=30))
    db.flush()
    return dict(db=db, home=home, away=away, other=other, analysed=analysed, league=league, cup=cup, history=rows)


def _rows(world):
    matches = ma.finished_matches(world["db"], [world["home"].id], world["analysed"].kickoff_utc)
    return [ma.team_row(m, world["home"].id) for m in matches]


def test_pre_match_sample_excludes_the_analysed_match_and_later_ones(world):
    rows = _rows(world)
    ids = {r["match_id"] for r in rows}
    assert world["analysed"].id not in ids and len(rows) == 12
    assert all(r["goals_for"] != 9 and r["goals_for"] != 7 for r in rows)
    assert [r["kickoff"] for r in rows] == sorted((r["kickoff"] for r in rows), reverse=True)


def test_windows_venue_and_competition_filters(world):
    rows = _rows(world)
    for window in (5, 10, 20):
        assert len(ma.select_rows(rows, window=window)) == min(window, 12)
    assert {r["venue"] for r in ma.select_rows(rows, venue="home")} == {"home"}
    assert len(ma.select_rows(rows, venue="away", window=20)) == 6
    assert len(ma.select_rows(rows, competition="analysis_cup")) == 2
    assert len(ma.select_rows(rows, competition="analysis_league", window=20)) == 10


def test_missing_statistics_are_not_zeros_and_sample_size_is_real(world):
    rows = ma.select_rows(_rows(world), window=10)
    profile = ma.profile(rows)
    assert profile["matches"] == 10
    assert profile["corners_total"]["n"] == 8  # two fixtures without statistics
    assert profile["goals_total"]["n"] == 10
    assert profile["possession"]["n"] == 0 and profile["possession"]["mean"] is None
    assert profile["corners_lines"]["8.5"]["n"] == 8
    assert sum(profile["corners_total"]["distribution"].values()) == 8


def test_line_frequencies_and_describe():
    freq = ma.line_frequencies([1, 2, 3, None, 4], (1.5, 2.5, 3.5))
    assert freq["1.5"] == dict(over=3, n=4, rate=.75)
    assert freq["3.5"] == dict(over=1, n=4, rate=.25)
    stats = ma.describe([8, 10, None, 12])
    assert (stats["n"], stats["mean"], stats["median"], stats["min"], stats["max"]) == (3, 10, 10, 8, 12)
    assert stats["distribution"] == {8: 1, 10: 1, 12: 1}
    assert ma.describe([None])["n"] == 0


def test_rates_and_shots(world):
    rows = ma.select_rows(_rows(world), window=12)
    p = ma.profile(rows)
    assert p["record"]["W"] + p["record"]["D"] + p["record"]["L"] == 12
    assert p["btts"]["n"] == 12
    assert p["shots_on_target_for"]["n"] == 10 and p["shots_off_target_for"]["mean"] is not None
    assert p["conversion"]["n"] == 10


def test_head_to_head_and_age(world):
    rows = _rows(world)
    h2h = ma.head_to_head(rows, world["away"].id, now=world["analysed"].kickoff_utc)
    assert len(h2h["matches"]) == 2
    assert [m["age_days"] for m in h2h["matches"]] == [7, 14]


def test_match_analysis_endpoint_builder(world):
    from backend.app.api.routes.analysis import build_match_analysis

    payload = build_match_analysis(world["db"], world["analysed"].id, 5, "split", "all", "all")
    home = payload["home"]["analysis"]
    assert home["filters"]["venue"] == "home" and home["profile"]["matches"] == 5
    assert all(m["venue"] == "home" for m in home["matches"])
    assert payload["final"]["home_corners"] == 30  # final statistics kept apart from the pre-match sample
    assert all(m["corners_for"] != 30 for m in home["matches"])
    scoped = build_match_analysis(world["db"], world["analysed"].id, 20, "all", "competition", "all")
    assert all(m["competition"] == "analysis_league" for m in scoped["home"]["analysis"]["matches"])


def test_explorer_orders_and_filters_by_sample(world, monkeypatch):
    from backend.app.api.routes import analysis
    from backend.app.db.models.matches import Match

    db = world["db"]
    fixtures = [Match(provider="test", provider_id=f"analysis-next-{i}", competition_id=world["league"].id,
                      season_id=world["history"][0].season_id, kickoff_utc=BASE + dt.timedelta(days=10, hours=i),
                      status="scheduled", home_team_id=home.id, away_team_id=away.id)
                for i, (home, away) in enumerate(((world["home"], world["other"]), (world["other"], world["away"])))]
    db.add_all(fixtures)
    db.flush()
    monkeypatch.setattr(analysis, "_now", lambda: BASE + dt.timedelta(days=5))
    kwargs = dict(date_from=(BASE + dt.timedelta(days=9)).date(), date_to=(BASE + dt.timedelta(days=11)).date(),
                  competition="analysis_league", team_id=None, metric="corners_avg", family="corners", line=None,
                  window=10, venue="all", min_sample=0, market=None)
    desc = analysis.build_explorer(db, order="desc", **kwargs)["rows"]
    asc = analysis.build_explorer(db, order="asc", **kwargs)["rows"]
    assert [r["match_id"] for r in desc] == [r["match_id"] for r in asc][::-1]
    assert desc[0]["value"] >= desc[-1]["value"]
    assert all(r["sample"]["home"] <= 10 for r in desc)
    strict = analysis.build_explorer(db, order="desc", **dict(kwargs, min_sample=60))["rows"]
    assert strict == []
    line = analysis.build_explorer(db, order="desc", **dict(kwargs, metric="line", line=8.5))["rows"]
    assert all(0 <= r["value"] <= 1 and r["counts"] is not None for r in line)


def test_team_profile_and_directory(world, monkeypatch):
    from backend.app.api.routes import analysis

    monkeypatch.setattr(analysis, "_now", lambda: BASE - dt.timedelta(days=1))
    profile = analysis.build_team_profile(world["db"], world["home"].id, 10, None)
    assert profile["overall"]["profile"]["matches"] == 10
    assert profile["home"]["profile"]["matches"] + profile["away"]["profile"]["matches"] == 12
    assert {c["code"] for c in profile["competitions"]} == {"analysis_league", "analysis_cup"}
    assert analysis.build_team_profile(world["db"], 10**8, 10, None) is None


def test_odds_centre_history_no_vig_and_freshness(world, monkeypatch):
    from backend.app.api.routes import analysis
    from backend.app.db.models.matches import Match, MatchOdds, MatchOddsHistory

    db = world["db"]
    match = Match(provider="test", provider_id="analysis-odds", competition_id=world["league"].id,
                  season_id=world["history"][0].season_id, kickoff_utc=BASE + dt.timedelta(days=20),
                  status="scheduled", home_team_id=world["home"].id, away_team_id=world["away"].id)
    db.add(match)
    db.flush()
    t0 = BASE + dt.timedelta(days=18)
    for i, price in enumerate((1.90, 1.90, 1.80)):  # one repeated price must not create a new point
        db.add(MatchOddsHistory(match_id=match.id, bookmaker="BookA", market="over_under_goals", line=2.5,
                                selection="over", price=price, snapshot_type="pre_match",
                                recorded_at=t0 + dt.timedelta(hours=i), provider_updated_at=t0, fetched_at=t0))
    db.add(MatchOddsHistory(match_id=match.id, bookmaker="BookA", market="over_under_goals", line=2.5, selection="over",
                            price=5.0, snapshot_type="pre_match", recorded_at=match.kickoff_utc + dt.timedelta(minutes=5)))
    fetched = BASE + dt.timedelta(days=19, hours=23)
    for selection, price in (("over", 1.80), ("under", 2.00)):
        db.add(MatchOdds(match_id=match.id, bookmaker="BookA", market="over_under_goals", line=2.5, selection=selection,
                         price=price, snapshot_type="pre_match", provider_updated_at=t0, fetched_at=fetched,
                         recorded_at=fetched))
    db.flush()
    monkeypatch.setattr(analysis, "_now", lambda: BASE + dt.timedelta(days=20) - dt.timedelta(minutes=30))
    payload = analysis.build_match_odds(db, match.id)
    over = next(r for r in payload["rows"] if r["selection"] == "over")
    assert [p["price"] for p in over["series"]] == [1.90, 1.80]  # post-kickoff price excluded, duplicate collapsed
    assert over["first"]["price"] == 1.90
    assert over["no_vig"] == pytest.approx((1 / 1.8) / (1 / 1.8 + 1 / 2.0), abs=1e-4)
    assert over["bookmakers"][0]["freshness"] == "vigente"
    assert over["bookmakers"][0]["origin_at"] != over["bookmakers"][0]["fetched_at"]


def test_competition_overview_and_coverage(world):
    from backend.app.api.routes.analysis import build_competition

    overview = build_competition(world["db"], "analysis_league")
    assert overview["competition"]["name"] == "Analysis League"
    assert overview["recent"] and overview["standings"] == []
    seasons = {row["season"]: row for row in overview["coverage"]}
    assert seasons["analysis-31"]["finished"] == 12  # 10 league history + analysed + later
    # Two fixtures lack statistics; the analysed one has corners but no shots: counts stay per field.
    assert (seasons["analysis-31"]["corners"], seasons["analysis-31"]["shots"]) == (9, 8)
    assert build_competition(world["db"], "missing_code") is None


def test_analysis_is_read_only_for_predictions_bank_and_laboratory(world, monkeypatch):
    from backend.app.api.routes import analysis
    from backend.app.db.models.modeling import Prediction
    from backend.app.db.models.publication import (
        BotManualTicket, BotTrialCut, BotTrialDecision, PreMatchForecast, StrategyCapture,
    )

    db = world["db"]
    tables = (Prediction, BotTrialDecision, BotTrialCut, BotManualTicket, StrategyCapture, PreMatchForecast)
    before = {t.__tablename__: db.query(t).count() for t in tables}
    snapshot = [(p.id, p.model_probability, p.calibrated_probability) for p in db.query(Prediction)]
    monkeypatch.setattr(analysis, "_now", lambda: BASE - dt.timedelta(days=1))
    analysis.build_match_analysis(db, world["analysed"].id, 10, "split", "all", "all")
    analysis.build_match_odds(db, world["analysed"].id)
    analysis.build_team_profile(db, world["home"].id, 10, None)
    analysis.build_competition(db, "analysis_league")
    db.flush()
    assert {t.__tablename__: db.query(t).count() for t in tables} == before
    assert [(p.id, p.model_probability, p.calibrated_probability) for p in db.query(Prediction)] == snapshot
    assert not db.new and not db.dirty and not db.deleted


def test_strategy_picks_rank_eligible_first_and_fill_with_near_misses():
    from types import SimpleNamespace

    from backend.app.api.routes.analysis import strategy_picks_from

    markets = {
        "over_1_5": {"probability": .80, "odds": 1.35, "ev": .08, "score": .60, "reasons": [], "confidence_reasons": []},
        "home_win": {"probability": .70, "odds": 1.60, "ev": .12, "score": .70, "reasons": [], "confidence_reasons": ["x"]},
        "btts": {"probability": .66, "odds": 1.70, "ev": .12, "score": .65, "reasons": [], "confidence_reasons": []},
        "over_2_5": {"probability": .62, "ev": .02, "reasons": ["probabilidad_inferior_al_65"]},
        "double_chance_1x": {"probability": .91, "odds": 1.10, "reasons": ["cuota_fuera_de_banda", "ev_estimado_no_positivo"]},
    }
    result = strategy_picks_from(markets)
    assert result["eligible_count"] == 3
    # High-confidence picks rank before the one with confidence caveats, then by score.
    assert [p["market"] for p in result["picks"]] == ["btts", "over_1_5", "home_win"]
    extra = {"home_team_over_0_5": {"probability": .88, "odds": 1.12, "reasons": ["cuota_fuera_de_banda"]},
             "away_win": {"probability": .55, "odds": 2.10, "reasons": ["probabilidad_inferior_al_65"]},
             "over_3_5": {"probability": .58, "odds": 2.60, "reasons": ["cuota_fuera_de_banda"]},
             "draw": {"probability": .25, "odds": 3.60, "reasons": ["probabilidad_inferior_al_65"]}}
    few = strategy_picks_from({**{k: markets[k] for k in ("over_1_5", "over_2_5", "double_chance_1x")}, **extra})
    # Fillers: most likely at odds 1.40–2.50 (over_2_5 at ~1.65, then away_win); 1.10, 1.12 and 2.60 are left out.
    assert [p["market"] for p in few["picks"]] == ["over_1_5", "over_2_5", "away_win"]
    assert few["picks"][1]["eligible"] is False and few["picks"][1]["odds"] == pytest.approx(1.02 / .62, abs=1e-3)
    finished = SimpleNamespace(status="finished", home_goals=2, away_goals=1, statistics=None)
    graded = strategy_picks_from({"over_1_5": markets["over_1_5"], "btts": markets["btts"]}, finished)
    assert {p["market"]: p["is_correct"] for p in graded["picks"]} == {"over_1_5": True, "btts": True}
