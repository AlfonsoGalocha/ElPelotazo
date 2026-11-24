from datetime import datetime, timedelta

from backend.app.db.models.matches import Match
from backend.app.ingestion.api_football.client import LEAGUE_IDS
from backend.app.ingestion.api_football.fixtures_provider import ApiFootballMatchProvider
from backend.app.normalization.competitions import CHAMPIONS_LEAGUE, COMPETITIONS
from backend.app.services.champions_statistics import regulation_statistics
from backend.app.services.data_service import ingest_matches
from backend.app.services.prediction_service import generate_predictions_for_competition
from backend.app.services.publication_service import selection_outcome
from backend.app.services.round_service import get_current_round, get_next_round
from backend.app.services.strategy_shadow import BANDS, LEAGUES, WEIGHTS, strategy_report


def fixture(key, status, round_name, date):
    return {
        "fixture": {"id": key, "status": {"short": status}, "date": date.isoformat()},
        "league": {"round": round_name},
        "teams": {"home": {"id": 5, "name": "Club Test A"}, "away": {"id": 6, "name": "Club Test B"}},
        "goals": {"home": 3, "away": 2},
        "score": {"fulltime": {"home": 1, "away": 1}},
        "statistics": [{"team": {"id": 6}, "statistics": [{"type": "Corner Kicks", "value": 4}]},
                       {"team": {"id": 5}, "statistics": [{"type": "Corner Kicks", "value": 5}]}],
    }


def test_champions_source_rounds_and_identity(db_session, monkeypatch):
    now = datetime.utcnow()
    data = [
        fixture(1, "PEN", "Quarter-finals", now-timedelta(days=1)),
        fixture(2, "NS", "Semi-finals", now+timedelta(days=1)),
        fixture(3, "NS", "Final", now+timedelta(days=8)),
        fixture(4, "1H", "League Stage - 3", now-timedelta(minutes=2)),
    ]
    provider = ApiFootballMatchProvider()
    monkeypatch.setattr(provider, "is_available", lambda: True)
    calls = []
    def fetch(path, params):
        calls.append((path, params))
        return data
    monkeypatch.setattr(provider, "_get", fetch)
    assert COMPETITIONS[CHAMPIONS_LEAGUE][0] == "UEFA Champions League"
    assert CHAMPIONS_LEAGUE in LEAGUES and len(WEIGHTS) == 8
    assert BANDS["principal"] == (1.35, 1.90)
    for _ in range(2):
        assert ingest_matches(db_session, provider, CHAMPIONS_LEAGUE, "2026/27") == 4
    assert calls == [("/fixtures", {"league": 2, "season": 2026})]*2
    matches = {m.provider_id: m for m in db_session.query(Match).filter(
        Match.competition.has(code=CHAMPIONS_LEAGUE)
    )}
    assert len(matches) == 4
    finished = matches["api-football-1"]
    assert (finished.home_goals, finished.away_goals, finished.round_name, finished.matchday) == (1, 1, "Quarter-finals", None)
    assert selection_outcome("draw", finished.home_goals, finished.away_goals, None) is True
    assert matches["api-football-4"].status == "live"
    assert matches["api-football-4"].matchday == 3
    assert get_current_round(db_session, CHAMPIONS_LEAGUE).round_name == "Semi-finals"
    assert get_next_round(db_session, CHAMPIONS_LEAGUE).round_name == "Final"
    assert strategy_report(db_session, competition=CHAMPIONS_LEAGUE)["fixtures"] == 0
    assert generate_predictions_for_competition(db_session, CHAMPIONS_LEAGUE) == []


def test_regulation_only_statistics_and_unchanged_league_contract():
    ft = fixture(1, "FT", "League Stage - 3", datetime(2026, 11, 23))
    aet = fixture(2, "AET", "Final", datetime(2026, 11, 23))
    assert regulation_statistics(ft) == {"away_corners": 4, "home_corners": 5}
    assert regulation_statistics(aet) == {}
    assert {k: LEAGUE_IDS[k] for k in ("laliga", "premier_league", "bundesliga", "serie_a", "ligue_1")} == {
        "laliga": 140, "premier_league": 39, "bundesliga": 78, "serie_a": 135, "ligue_1": 61,
    }


def test_liga_f_provider_identity_and_shadow_scope(monkeypatch):
    from backend.app.ingestion.api_football.client import LEAGUE_IDS
    from backend.app.normalization.competitions import LIGA_F, competition_team_name

    now = datetime.utcnow()
    provider = ApiFootballMatchProvider()
    calls = []

    def fetch(path, params):
        calls.append((path, params))
        return [fixture(14201, "NS", "Regular Season - 12", now + timedelta(days=1))]

    monkeypatch.setattr(provider, "_get", fetch)
    row, = provider.fetch_matches(LIGA_F, "2026/27")
    assert calls == [("/fixtures", {"league": 142, "season": 2026})]
    assert LEAGUE_IDS[LIGA_F] == 142
    assert row.matchday == 12
    assert row.provider_status == "NS"
    assert row.home_team_raw.endswith(" · Liga F")
    assert competition_team_name("Barcelona", LIGA_F) != competition_team_name("Barcelona", "laliga")
    assert LIGA_F in LEAGUES
    assert len(WEIGHTS) == 8


def test_afc_elite_provider_round_and_shadow_scope(monkeypatch):
    from backend.app.normalization.competitions import AFC_CHAMPIONS_LEAGUE, CONTINENTAL_CHAMPIONS

    provider = ApiFootballMatchProvider()
    now = datetime.utcnow()
    calls = []

    def fetch(path, params):
        calls.append((path, params))
        return [
            fixture(17001, "NS", "League Phase - 5", now + timedelta(days=1)),
            fixture(17002, "1H", "League Phase - 5", now - timedelta(minutes=5)),
            fixture(17003, "NS", "Round of 16 - 1", now + timedelta(days=3)),
        ]

    monkeypatch.setattr(provider, "_get", fetch)
    scheduled, live, knockout = provider.fetch_matches(AFC_CHAMPIONS_LEAGUE, "2026/27")
    assert calls == [("/fixtures", {"league": 17, "season": 2026})]
    assert scheduled.matchday == 5 and scheduled.round_name == "League Phase - 5"
    assert live.status == "live"
    assert knockout.matchday is None
    assert AFC_CHAMPIONS_LEAGUE in CONTINENTAL_CHAMPIONS and AFC_CHAMPIONS_LEAGUE in LEAGUES
    assert len(WEIGHTS) == 8
