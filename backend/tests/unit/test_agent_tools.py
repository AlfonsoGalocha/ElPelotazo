"""Tests de la tool get_matches_today (sin ningun LLM real de por medio --
el LLM se testea aparte, mockeado, en test_agent_api.py). La tool reusa
`round_service`/`best_prediction_per_match`, ya testeados; aqui solo se
comprueba que el WRAPPER estructura bien la salida y respeta las mismas
reglas que el Home (partidos pasados fuera, mejor prediccion por
signal_score)."""

from __future__ import annotations

import datetime as dt

from backend.app.agent.tools import get_matches_today
from backend.app.db.models.core import Competition, Season, Team
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction


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


def _make_model_version(db_session) -> ModelVersion:
    mv = ModelVersion(
        name="test-model",
        version="1",
        market_family="goals",
        features=[],
        hyperparameters={},
        metrics={},
        dataset_version="test",
    )
    db_session.add(mv)
    db_session.flush()
    return mv


def test_get_matches_today_excludes_past_matches_and_includes_best_prediction(db_session):
    competition, season = _make_competition_with_season(db_session, "agent_league")
    home, away = _make_team(db_session, "Home FC"), _make_team(db_session, "Away FC")
    model_version = _make_model_version(db_session)
    now = dt.datetime.utcnow()

    past_match = Match(
        provider="test", provider_id="past1", competition_id=competition.id, season_id=season.id,
        kickoff_utc=now - dt.timedelta(hours=3), home_team_id=home.id, away_team_id=away.id,
        status="scheduled", matchday=1,
    )
    upcoming_match = Match(
        provider="test", provider_id="up1", competition_id=competition.id, season_id=season.id,
        kickoff_utc=now + dt.timedelta(hours=3), home_team_id=home.id, away_team_id=away.id,
        status="scheduled", matchday=1,
    )
    db_session.add_all([past_match, upcoming_match])
    db_session.flush()

    db_session.add(
        Prediction(
            match_id=upcoming_match.id,
            market="over_2_5",
            selection="over",
            model_version_id=model_version.id,
            model_probability=0.65,
            market_probability=0.55,
            market_odds=1.9,
            fair_odds=round(1 / 0.65, 4),
            edge=0.10,
            confidence=0.8,
            data_quality=0.9,
            signal_score=0.5,
            bookmakers_count=8,
            bookmakers_used=8,
            explanation={},
            features_used={},
        )
    )
    db_session.flush()

    result = get_matches_today(db_session, {})

    match_ids = [m["match_id"] for m in result["matches"]]
    assert past_match.id not in match_ids
    assert upcoming_match.id in match_ids

    entry = next(m for m in result["matches"] if m["match_id"] == upcoming_match.id)
    assert entry["home_team"] == "Home FC"
    assert entry["away_team"] == "Away FC"
    assert entry["best_prediction"] is not None
    assert entry["best_prediction"]["market"] == "over_2_5"
    assert entry["best_prediction"]["edge_pp"] == 10.0


def test_get_matches_today_reports_no_prediction_honestly(db_session):
    """Un partido programado sin ninguna prediccion generada todavia no
    debe aparecer con datos inventados -- simplemente no se incluye (nunca
    se rellena con un 'best_prediction' falso)."""
    competition, season = _make_competition_with_season(db_session, "agent_league_2")
    home, away = _make_team(db_session, "H2"), _make_team(db_session, "A2")
    now = dt.datetime.utcnow()
    db_session.add(
        Match(
            provider="test", provider_id="np1", competition_id=competition.id, season_id=season.id,
            kickoff_utc=now + dt.timedelta(hours=1), home_team_id=home.id, away_team_id=away.id,
            status="scheduled", matchday=1,
        )
    )
    db_session.flush()

    result = get_matches_today(db_session, {})
    assert all(m["competition_code"] != "agent_league_2" for m in result["matches"])


def test_get_matches_today_well_formed_for_a_competition_with_no_matches(db_session):
    """No asume que la BD entera este vacia (otros tests de la suite
    confirman datos fuera de la transaccion de este fixture, mismo patron
    ya existente en test_round_service.py) -- solo que una competicion
    recien creada, sin partidos, nunca aparece en el resultado."""
    _make_competition_with_season(db_session, "agent_league_empty")
    result = get_matches_today(db_session, {})
    assert isinstance(result["total_matches"], int)
    assert all(m["competition_code"] != "agent_league_empty" for m in result["matches"])
