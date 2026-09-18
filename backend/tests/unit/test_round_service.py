from __future__ import annotations

import datetime as dt

from backend.app.db.models.core import Competition, Season, Team
from backend.app.db.models.matches import Match
from backend.app.services.round_service import get_current_round


def _make_competition_with_season(db_session, code: str = "test_league") -> tuple[Competition, Season]:
    competition = Competition(code=code, name="Test League", country="Testland")
    db_session.add(competition)
    db_session.flush()
    season = Season(competition_id=competition.id, label="2025/26")
    db_session.add(season)
    db_session.flush()
    return competition, season


def _make_team(db_session, name: str) -> Team:
    team = Team(canonical_name=name)
    db_session.add(team)
    db_session.flush()
    return team


def _make_match(
    db_session, competition, season, home, away, kickoff, status, matchday, provider_id
):
    match = Match(
        provider="test",
        provider_id=provider_id,
        competition_id=competition.id,
        season_id=season.id,
        kickoff_utc=kickoff,
        home_team_id=home.id,
        away_team_id=away.id,
        status=status,
        matchday=matchday,
    )
    db_session.add(match)
    db_session.flush()
    return match


def test_current_round_is_lowest_matchday_with_pending_matches(db_session):
    """Jornada 5 pendiente y jornada 6 ya con fecha: la jornada actual debe
    seguir siendo la 5, no "los proximos partidos que haya"."""
    competition, season = _make_competition_with_season(db_session)
    teams = [_make_team(db_session, f"Team {i}") for i in range(6)]
    now = dt.datetime.utcnow()

    # Jornada 5: dos partidos ya jugados, uno pendiente (aplazado).
    _make_match(db_session, competition, season, teams[0], teams[1], now - dt.timedelta(days=10), "finished", 5, "m1")
    _make_match(db_session, competition, season, teams[2], teams[3], now - dt.timedelta(days=9), "finished", 5, "m2")
    pending_j5 = _make_match(
        db_session, competition, season, teams[4], teams[5], now + dt.timedelta(days=1), "scheduled", 5, "m3"
    )

    # Jornada 6: todavia no jugada, pero YA tiene fecha (mas cercana incluso).
    _make_match(db_session, competition, season, teams[0], teams[2], now + dt.timedelta(hours=1), "scheduled", 6, "m4")

    round_info = get_current_round(db_session, "test_league")
    assert round_info is not None
    assert round_info.round == 5
    assert round_info.match_ids == [
        m.id
        for m in db_session.query(Match)
        .filter(Match.competition_id == competition.id, Match.matchday == 5)
        .order_by(Match.id)
        .all()
    ]
    assert pending_j5.id in round_info.match_ids
    assert round_info.next_round == 6
    assert round_info.is_fallback is False


def test_future_round_excluded_when_current_round_still_pending(db_session):
    competition, season = _make_competition_with_season(db_session, "test_league_2")
    teams = [_make_team(db_session, f"T{i}") for i in range(4)]
    now = dt.datetime.utcnow()

    _make_match(db_session, competition, season, teams[0], teams[1], now + dt.timedelta(days=1), "scheduled", 3, "a1")
    _make_match(db_session, competition, season, teams[2], teams[3], now + dt.timedelta(days=8), "scheduled", 4, "a2")

    round_info = get_current_round(db_session, "test_league_2")
    assert round_info.round == 3
    assert all(mid != _match_id_for(db_session, "a2") for mid in round_info.match_ids)


def _match_id_for(db_session, provider_id: str) -> int:
    return db_session.query(Match).filter_by(provider_id=provider_id).one().id


def test_round_advances_once_current_round_fully_finished(db_session):
    competition, season = _make_competition_with_season(db_session, "test_league_3")
    teams = [_make_team(db_session, f"U{i}") for i in range(4)]
    now = dt.datetime.utcnow()

    _make_match(db_session, competition, season, teams[0], teams[1], now - dt.timedelta(days=2), "finished", 1, "b1")
    _make_match(db_session, competition, season, teams[2], teams[3], now + dt.timedelta(days=3), "scheduled", 2, "b2")

    round_info = get_current_round(db_session, "test_league_3")
    assert round_info.round == 2


def test_falls_back_to_date_window_when_no_matchday_data(db_session):
    """Si los partidos programados no tienen `matchday` (p.ej. porque se
    ingirieron antes de que este campo existiera), no se debe fingir una
    jornada: se cae a una ventana de fechas y se marca explicitamente."""
    competition, season = _make_competition_with_season(db_session, "test_league_4")
    teams = [_make_team(db_session, f"V{i}") for i in range(2)]
    now = dt.datetime.utcnow()

    _make_match(
        db_session, competition, season, teams[0], teams[1], now + dt.timedelta(days=1), "scheduled", None, "c1"
    )

    round_info = get_current_round(db_session, "test_league_4")
    assert round_info.is_fallback is True
    assert round_info.round is None
    assert len(round_info.match_ids) == 1


def test_unknown_competition_returns_none(db_session):
    assert get_current_round(db_session, "does_not_exist") is None
