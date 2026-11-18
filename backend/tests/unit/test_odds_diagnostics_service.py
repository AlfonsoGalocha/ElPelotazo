"""Tests del panel tecnico de diagnostico de cuotas (seccion
"Configuracion", pedido real del usuario tras investigar por que la app
mostraba cuotas desactualizadas): `OddsIngestionRun` (registro real de
cada intento de `update-odds`/`update-secondary-odds`) y la evidencia ya
guardada en `MatchOdds` (frescura/casas vistas)."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.app.db.models.core import OddsIngestionRun
from backend.app.db.models.matches import Match, MatchOdds
from backend.app.services.odds_diagnostics_service import (
    latest_runs_by_competition,
    market_odds_freshness,
    record_ingestion_run,
)


@pytest.fixture(autouse=True)
def _clean_odds_diagnostics_tables(db_session):
    """`latest_runs_by_competition`/`market_odds_freshness` leen SIN
    filtro por diseno (el panel real quiere el estado GLOBAL) -- en la
    suite completa, otros tests (integracion incluida) ya dejaron filas
    reales confirmadas (commit, no rollback) en estas mismas tablas antes
    de que corra este archivo. Borrarlas aqui, DENTRO de la transaccion
    aislada de `db_session`, le da a cada test un estado en cero sin
    tocar los datos reales de otros tests (se revierte solo al cerrar la
    transaccion, igual que el resto de lo que escribe este archivo)."""
    db_session.query(OddsIngestionRun).delete()
    db_session.query(MatchOdds).delete()
    db_session.flush()


def _match(db, match_id_hint: int, status: str = "scheduled") -> Match:
    match = Match(
        provider="test",
        provider_id=f"odds-diag-fixture-{match_id_hint}",
        competition_id=1,
        season_id=1,
        kickoff_utc=dt.datetime.utcnow() + dt.timedelta(days=1),
        home_team_id=1,
        away_team_id=2,
        status=status,
    )
    db.add(match)
    db.flush()
    return match


def test_record_ingestion_run_persists_a_successful_attempt(db_session):
    started = dt.datetime(2026, 11, 18, 12, 0, 0)
    finished = dt.datetime(2026, 11, 18, 12, 0, 5)
    record_ingestion_run(
        db_session, "api_football", "update_odds", "laliga", started, finished, fixtures_fetched=20, matched=18
    )

    runs = latest_runs_by_competition(db_session)
    assert len(runs) == 1
    assert runs[0].provider == "api_football"
    assert runs[0].operation == "update_odds"
    assert runs[0].competition_code == "laliga"
    assert runs[0].fixtures_fetched == 20
    assert runs[0].matched == 18
    assert runs[0].error is None


def test_record_ingestion_run_persists_a_failed_attempt_without_inventing_counts(db_session):
    started = dt.datetime(2026, 11, 18, 12, 0, 0)
    finished = dt.datetime(2026, 11, 18, 12, 0, 1)
    record_ingestion_run(
        db_session, "api_football", "update_odds", "premier_league", started, finished, error="rate limit"
    )

    runs = latest_runs_by_competition(db_session)
    assert len(runs) == 1
    assert runs[0].error == "rate limit"
    # Nunca 0 inventado cuando el fallo fue antes de contar nada -- None
    # real, distinto de "la API respondio con 0 partidos".
    assert runs[0].fixtures_fetched is None
    assert runs[0].matched is None


def test_latest_runs_by_competition_only_returns_the_most_recent_per_key(db_session):
    """Dos intentos reales de la MISMA (provider, operation, competicion)
    -- solo el mas reciente debe aparecer en el panel, nunca los dos (el
    historico completo sigue en la tabla, append-only, solo el panel
    resume "el ultimo")."""
    old = dt.datetime(2026, 11, 18, 6, 0, 0)
    new = dt.datetime(2026, 11, 18, 12, 0, 0)
    record_ingestion_run(db_session, "api_football", "update_odds", "laliga", old, old, fixtures_fetched=5, matched=5)
    record_ingestion_run(
        db_session, "api_football", "update_odds", "laliga", new, new, fixtures_fetched=20, matched=19
    )

    runs = latest_runs_by_competition(db_session)
    assert len(runs) == 1
    assert runs[0].finished_at == new
    assert runs[0].fixtures_fetched == 20


def test_latest_runs_by_competition_keeps_separate_keys_distinct(db_session):
    """Dos competiciones (o dos operaciones) distintas nunca se mezclan
    entre si, aunque se ejecuten en el mismo instante."""
    when = dt.datetime(2026, 11, 18, 12, 0, 0)
    record_ingestion_run(db_session, "api_football", "update_odds", "laliga", when, when, fixtures_fetched=1)
    record_ingestion_run(db_session, "api_football", "update_odds", "premier_league", when, when, fixtures_fetched=2)
    record_ingestion_run(
        db_session, "api_football", "update_secondary_odds", "laliga", when, when, fixtures_fetched=3
    )

    runs = latest_runs_by_competition(db_session)
    assert len(runs) == 3
    keys = {(r.operation, r.competition_code) for r in runs}
    assert keys == {("update_odds", "laliga"), ("update_odds", "premier_league"), ("update_secondary_odds", "laliga")}


def test_market_odds_freshness_reports_none_without_any_odds_recorded(db_session):
    freshness = market_odds_freshness(db_session)
    assert freshness["most_recent_odds_recorded_at"] is None
    assert freshness["bookmakers_seen"] == []
    assert freshness["scheduled_matches_with_odds"] == 0


def test_market_odds_freshness_reflects_real_matchodds_rows(db_session):
    match = _match(db_session, 1, status="scheduled")
    older = dt.datetime(2026, 11, 18, 6, 0, 0)
    newer = dt.datetime(2026, 11, 18, 16, 18, 52)
    db_session.add_all(
        [
            MatchOdds(
                match_id=match.id,
                bookmaker="Bet365",
                market="over_under_goals",
                line=2.5,
                selection="over",
                price=1.73,
                snapshot_type="pre_match",
                recorded_at=older,
            ),
            MatchOdds(
                match_id=match.id,
                bookmaker="Pinnacle",
                market="over_under_goals",
                line=2.5,
                selection="over",
                price=1.79,
                snapshot_type="pre_match",
                recorded_at=newer,
            ),
        ]
    )
    db_session.flush()

    freshness = market_odds_freshness(db_session)
    assert freshness["most_recent_odds_recorded_at"] == newer
    assert freshness["bookmakers_seen"] == ["Bet365", "Pinnacle"]
    assert freshness["scheduled_matches_with_odds"] == 1


def test_market_odds_freshness_never_counts_a_finished_match_as_scheduled(db_session):
    finished_match = _match(db_session, 2, status="finished")
    db_session.add(
        MatchOdds(
            match_id=finished_match.id,
            bookmaker="Bet365",
            market="over_under_goals",
            line=2.5,
            selection="over",
            price=1.73,
            snapshot_type="pre_match",
            recorded_at=dt.datetime.utcnow(),
        )
    )
    db_session.flush()

    freshness = market_odds_freshness(db_session)
    assert freshness["scheduled_matches_with_odds"] == 0
