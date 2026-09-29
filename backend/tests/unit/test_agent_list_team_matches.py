"""Tests de la tool list_team_matches (pedido real de usuario: 'que
partidos le quedan al Barca', 'como le fue al Sevilla el mes pasado')."""

from __future__ import annotations

import datetime as dt

from backend.app.agent.tools import list_team_matches
from backend.app.db.models.core import Competition, Season, Team
from backend.app.db.models.matches import Match


def _make_competition_with_season(db_session, code: str) -> tuple[Competition, Season]:
    competition = Competition(code=code, name=code.replace("_", " ").title(), country="Testland")
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


def _make_match(db_session, competition, season, home, away, kickoff, **overrides) -> Match:
    defaults = dict(
        provider="test", provider_id=f"m{kickoff.isoformat()}{home.id}{away.id}",
        competition_id=competition.id, season_id=season.id, kickoff_utc=kickoff,
        home_team_id=home.id, away_team_id=away.id, status="scheduled", matchday=1,
    )
    defaults.update(overrides)
    match = Match(**defaults)
    db_session.add(match)
    db_session.flush()
    return match


def test_list_team_matches_returns_upcoming_and_past_ordered_chronologically(db_session):
    competition, season = _make_competition_with_season(db_session, "team_matches_league")
    barca = _make_team(db_session, "Barcelona")
    rival1 = _make_team(db_session, "Getafe")
    rival2 = _make_team(db_session, "Osasuna")
    now = dt.datetime.utcnow()

    past = _make_match(
        db_session, competition, season, rival1, barca, now - dt.timedelta(days=7),
        status="finished", home_goals=1, away_goals=2,
    )
    upcoming = _make_match(db_session, competition, season, barca, rival2, now + dt.timedelta(days=3))

    result = list_team_matches(db_session, {"team": "Barcelona"})

    assert result["team"] == "Barcelona"
    assert result["total_matches"] == 2
    ids_in_order = [m["match_id"] for m in result["matches"]]
    assert ids_in_order == [past.id, upcoming.id]
    assert result["matches"][0]["final_score"] == "1-2"
    assert result["matches"][1]["final_score"] is None


def test_list_team_matches_scope_upcoming_excludes_past(db_session):
    competition, season = _make_competition_with_season(db_session, "team_matches_scope")
    team = _make_team(db_session, "Villarreal")
    rival = _make_team(db_session, "Elche")
    now = dt.datetime.utcnow()
    _make_match(db_session, competition, season, rival, team, now - dt.timedelta(days=1), status="finished", home_goals=0, away_goals=0)
    upcoming = _make_match(db_session, competition, season, team, rival, now + dt.timedelta(days=1))

    result = list_team_matches(db_session, {"team": "Villarreal", "scope": "upcoming"})

    assert result["total_matches"] == 1
    assert result["matches"][0]["match_id"] == upcoming.id


def test_list_team_matches_never_invents_an_unknown_team(db_session):
    result = list_team_matches(db_session, {"team": "Equipo Que No Existe Nunca Jamas"})
    assert "error" in result
    assert "matches" not in result


def test_list_team_matches_requires_team_param(db_session):
    result = list_team_matches(db_session, {})
    assert "error" in result


def test_list_team_matches_prefers_shortest_name_match_when_ambiguous(db_session):
    """'Madrid' hace substring tanto de 'Real Madrid' como de 'Atletico
    Madrid' -- se prioriza el nombre mas corto (menos ambiguo)."""
    competition, season = _make_competition_with_season(db_session, "team_matches_ambiguous")
    _make_team(db_session, "Atletico Madrid")
    real_madrid = _make_team(db_session, "Real Madrid")
    rival = _make_team(db_session, "Getafe")
    _make_match(db_session, competition, season, real_madrid, rival, dt.datetime.utcnow() + dt.timedelta(days=1))

    result = list_team_matches(db_session, {"team": "Madrid"})

    assert result["team"] == "Real Madrid"
