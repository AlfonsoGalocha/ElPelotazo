"""Tests de las tools de Fase 5 (analyze_match, get_model_performance,
get_active_alerts) y de la generacion de alertas de Fase 6
(agent/alerts.py). Sin ningun LLM de por medio -- eso se testea aparte,
mockeado."""

from __future__ import annotations

import datetime as dt

from backend.app.agent.alerts import generate_alerts_for_current_round
from backend.app.agent.tools import analyze_match, get_active_alerts, get_model_performance
from backend.app.config.settings import Settings
from backend.app.db.models.core import Competition, Season, Team
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import AgentAlert, ModelVersion, Prediction


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
        name="test-model", version="1", market_family="goals", features=[],
        hyperparameters={}, metrics={}, dataset_version="test",
    )
    db_session.add(mv)
    db_session.flush()
    return mv


def _make_prediction(db_session, match, model_version, **overrides):
    defaults = dict(
        match_id=match.id, market="over_2_5", selection="over", model_version_id=model_version.id,
        model_probability=0.6, market_probability=0.5, market_odds=2.0, fair_odds=round(1 / 0.6, 4),
        edge=0.10, confidence=0.8, data_quality=0.9, signal_score=0.4, bookmakers_count=8,
        bookmakers_used=8, explanation={}, features_used={},
    )
    defaults.update(overrides)
    prediction = Prediction(**defaults)
    db_session.add(prediction)
    db_session.flush()
    return prediction


def test_analyze_match_finds_match_by_two_team_names_and_includes_factors(db_session):
    competition, season = _make_competition_with_season(db_session, "phase5_league")
    home, away = _make_team(db_session, "Barcelona"), _make_team(db_session, "Getafe")
    model_version = _make_model_version(db_session)
    match = Match(
        provider="test", provider_id="am1", competition_id=competition.id, season_id=season.id,
        kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1), home_team_id=home.id,
        away_team_id=away.id, status="scheduled", matchday=1,
    )
    db_session.add(match)
    db_session.flush()
    _make_prediction(
        db_session, match, model_version,
        explanation={"factors": [{"feature": "home_form", "direction": "increases_probability"}]},
    )

    result = analyze_match(db_session, {"query": "Barcelona vs Getafe"})

    assert result["match_id"] == match.id
    assert result["home_team"] == "Barcelona"
    assert result["away_team"] == "Getafe"
    assert result["status"] == "scheduled"
    assert result["final_score"] is None
    assert len(result["predictions"]) == 1
    assert result["predictions"][0]["factors"] == [
        {"feature": "home_form", "direction": "increases_probability"}
    ]
    assert result["best_prediction"]["market"] == "over_2_5"


def test_analyze_match_finds_match_without_separator_by_recognizing_two_known_teams(db_session):
    """Bug real reportado por voz: 'Paris Saint-Germain Le Mans' (sin 'vs'/
    '-'/'contra', tal cual lo transcribe Whisper) antes se buscaba como el
    nombre LITERAL de un unico equipo y nunca encontraba nada. Ahora
    reconoce que dos equipos conocidos de la base de datos aparecen como
    substring de la consulta."""
    competition, season = _make_competition_with_season(db_session, "phase5_no_sep")
    home = _make_team(db_session, "Paris Saint-Germain")
    away = _make_team(db_session, "Le Mans")
    match = Match(
        provider="test", provider_id="am_nosep", competition_id=competition.id,
        season_id=season.id, kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1),
        home_team_id=home.id, away_team_id=away.id, status="scheduled", matchday=1,
    )
    db_session.add(match)
    db_session.flush()

    result = analyze_match(db_session, {"query": "Paris Saint-Germain Le Mans"})

    assert result["match_id"] == match.id
    assert result["home_team"] == "Paris Saint-Germain"
    assert result["away_team"] == "Le Mans"


def test_analyze_match_reports_final_score_for_finished_match(db_session):
    competition, season = _make_competition_with_season(db_session, "phase5_league_finished")
    home, away = _make_team(db_session, "Real Madrid"), _make_team(db_session, "Sevilla")
    match = Match(
        provider="test", provider_id="am2", competition_id=competition.id, season_id=season.id,
        kickoff_utc=dt.datetime.utcnow() - dt.timedelta(days=1), home_team_id=home.id,
        away_team_id=away.id, status="finished", home_goals=2, away_goals=1, matchday=1,
    )
    db_session.add(match)
    db_session.flush()

    result = analyze_match(db_session, {"query": "Real Madrid"})
    assert result["final_score"] == "2-1"
    assert result["predictions"] == []
    assert result["best_prediction"] is None


def test_analyze_match_never_invents_a_match_that_does_not_exist(db_session):
    result = analyze_match(db_session, {"query": "Equipo Que No Existe Nunca Jamas"})
    assert "error" in result
    assert result["error"]


def test_analyze_match_requires_query_param(db_session):
    result = analyze_match(db_session, {})
    assert "error" in result


def test_get_model_performance_delegates_to_real_performance_report(db_session):
    # Sin ninguna prediccion liquidada: real_performance_report ya se
    # testea a fondo en test_settlement.py, aqui solo se comprueba que la
    # tool delega bien los filtros sin romper.
    result = get_model_performance(db_session, {"competition_code": "no_existe", "market": "over_2_5"})
    assert isinstance(result, dict)


def test_get_active_alerts_reports_disabled_explicitly(db_session):
    result = get_active_alerts(db_session, {})
    assert result["enabled"] is False
    assert result["alerts"] == []
    assert "desactivadas" in result["note"]


def test_generate_alerts_creates_value_signal_for_strong_best_prediction(db_session):
    competition, season = _make_competition_with_season(db_session, "phase6_league")
    home, away = _make_team(db_session, "TeamX"), _make_team(db_session, "TeamY")
    model_version = _make_model_version(db_session)
    match = Match(
        provider="test", provider_id="al1", competition_id=competition.id, season_id=season.id,
        kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1), home_team_id=home.id,
        away_team_id=away.id, status="scheduled", matchday=1,
    )
    db_session.add(match)
    db_session.flush()
    _make_prediction(db_session, match, model_version, edge=0.15, confidence=0.8, data_quality=0.9)

    # agent_alert_leagues acotado a ESTA liga a proposito: otros tests de
    # la suite confirman datos reales fuera de la transaccion de
    # db_session (mismo patron ya existente en test_round_service.py), y
    # sin acotar, generate_alerts_for_current_round tambien los escanearia.
    settings = Settings(
        agent_alerts_enabled=True,
        agent_alert_min_edge_pp=5.0,
        agent_alert_min_tier="MEDIUM_DATA_SUPPORT",
        agent_alert_leagues=["phase6_league"],
    )
    created = generate_alerts_for_current_round(db_session, settings)
    assert created == 1

    alerts = db_session.query(AgentAlert).filter_by(match_id=match.id).all()
    assert len(alerts) == 1
    assert alerts[0].alert_type == "SENAL_DE_VALOR"
    assert "over 2.5" in alerts[0].message.lower()

    # Idempotencia: correrlo otra vez no duplica.
    created_again = generate_alerts_for_current_round(db_session, settings)
    assert created_again == 0
    assert db_session.query(AgentAlert).filter_by(match_id=match.id).count() == 1


def test_generate_alerts_disabled_by_default_creates_nothing(db_session):
    competition, season = _make_competition_with_season(db_session, "phase6_league_disabled")
    home, away = _make_team(db_session, "TeamZ"), _make_team(db_session, "TeamW")
    model_version = _make_model_version(db_session)
    match = Match(
        provider="test", provider_id="al2", competition_id=competition.id, season_id=season.id,
        kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1), home_team_id=home.id,
        away_team_id=away.id, status="scheduled", matchday=1,
    )
    db_session.add(match)
    db_session.flush()
    _make_prediction(db_session, match, model_version, edge=0.20, confidence=0.9, data_quality=0.9)

    created = generate_alerts_for_current_round(db_session, Settings(agent_alerts_enabled=False))
    assert created == 0
    assert db_session.query(AgentAlert).filter_by(match_id=match.id).count() == 0


def test_generate_alerts_flags_low_reliability_prediction(db_session):
    competition, season = _make_competition_with_season(db_session, "phase6_league_low_rel")
    home, away = _make_team(db_session, "TeamLR1"), _make_team(db_session, "TeamLR2")
    model_version = _make_model_version(db_session)
    match = Match(
        provider="test", provider_id="al3", competition_id=competition.id, season_id=season.id,
        kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1), home_team_id=home.id,
        away_team_id=away.id, status="scheduled", matchday=1,
    )
    db_session.add(match)
    db_session.flush()
    _make_prediction(db_session, match, model_version, confidence=0.2, data_quality=0.2, edge=0.01)

    settings = Settings(agent_alerts_enabled=True)
    created = generate_alerts_for_current_round(db_session, settings)
    assert created >= 1
    alert_types = {
        a.alert_type for a in db_session.query(AgentAlert).filter_by(match_id=match.id).all()
    }
    assert "SENAL_BAJA_FIABILIDAD" in alert_types


def test_get_active_alerts_lists_generated_alerts(db_session, monkeypatch):
    competition, season = _make_competition_with_season(db_session, "phase6_league_list")
    home, away = _make_team(db_session, "TeamL1"), _make_team(db_session, "TeamL2")
    match = Match(
        provider="test", provider_id="al4", competition_id=competition.id, season_id=season.id,
        kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1), home_team_id=home.id,
        away_team_id=away.id, status="scheduled", matchday=1,
    )
    db_session.add(match)
    db_session.flush()
    db_session.add(AgentAlert(match_id=match.id, alert_type="SENAL_DE_VALOR", message="test"))
    db_session.flush()

    monkeypatch.setattr(
        "backend.app.agent.tools.get_settings", lambda: Settings(agent_alerts_enabled=True)
    )
    result = get_active_alerts(db_session, {})
    assert result["enabled"] is True
    assert any(a["match_id"] == match.id for a in result["alerts"])
