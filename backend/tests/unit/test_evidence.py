"""Fase B de la revision v2.8: motor de evidencia estadistica por mercado.

Comprueba la pieza nueva de verdad (frecuencia historica real de cumplir
la linea, inexistente antes de esto) y su disciplina anti-leakage, usando
una base de datos SQLite aislada por test (fixture `db_session`).
"""

from __future__ import annotations

import datetime as dt
import itertools

from backend.app.db.models.core import ProviderResponse
from backend.app.db.models.matches import CornerObservation, Match, MatchStatistics
from backend.app.db.models.modeling import ModelVersion, Prediction
from backend.app.prediction.evidence import build_market_evidence

KICKOFF = dt.datetime(2025, 3, 1, 18, 0)

# Nunca fijar `Match.id` a mano (chocaba con filas commiteadas de forma
# permanente -- via `session_scope()`, nunca revertidas -- por otros
# ficheros de test sobre el mismo SQLite de sesion, p.ej.
# `test_ranking_comparison.py`): se deja que autoincremente, y se usa este
# contador solo para que `provider_id` sea unico entre llamadas.
_provider_id_seq = itertools.count(1)


def _match(
    db,
    home_team_id: int,
    away_team_id: int,
    kickoff: dt.datetime,
    home_goals: int | None,
    away_goals: int | None,
    season_id: int = 1,
    status: str = "finished",
) -> Match:
    match = Match(
        provider="evidence-test",
        provider_id=f"evidence-{next(_provider_id_seq)}",
        competition_id=1,
        season_id=season_id,
        kickoff_utc=kickoff,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        status=status,
        home_goals=home_goals,
        away_goals=away_goals,
    )
    db.add(match)
    db.flush()
    return match


def _model_version(db) -> ModelVersion:
    mv = ModelVersion(
        name="evidence-test", version="v1", market_family="goals",
        features=[], hyperparameters={}, metrics={}, dataset_version="test",
    )
    db.add(mv)
    db.flush()
    return mv


def _prediction(db, match_id: int, market: str, line: float | None, model_version_id: int) -> Prediction:
    prediction = Prediction(
        match_id=match_id, market=market, line=line, selection="over",
        model_version_id=model_version_id, model_probability=0.6, fair_odds=1.6,
        confidence=0.7, data_quality=0.7, explanation={}, features_used={},
        created_at=dt.datetime.utcnow(),
    )
    db.add(prediction)
    db.flush()
    return prediction


def test_over_under_pools_both_teams_and_computes_real_hit_frequency(db_session):
    db = db_session
    mv = _model_version(db)
    # IDs de equipo muy por encima del rango usado por otros ficheros de
    # test (que suelen usar 1/2/3...) para que ninguna fila commiteada de
    # forma permanente por otro test (via `session_scope()`) contamine las
    # ventanas de este -- ver comentario de `_provider_id_seq`.
    home, away = 910101, 910102
    # Historial de `home`: 3 anteriores, totales 3, 1, 4 (over 2.5: si,no,si).
    _match(db, home, 910110, KICKOFF - dt.timedelta(days=21), 2, 1)
    _match(db, home, 910111, KICKOFF - dt.timedelta(days=14), 1, 0)
    _match(db, 910112, home, KICKOFF - dt.timedelta(days=7), 2, 2)
    # Historial de `away`: 2 anteriores, totales 0, 5 (over 2.5: no,si).
    _match(db, away, 910120, KICKOFF - dt.timedelta(days=10), 0, 0)
    _match(db, 910121, away, KICKOFF - dt.timedelta(days=3), 3, 2)

    target = _match(db, home, away, KICKOFF, None, None, status="scheduled")
    prediction = _prediction(db, target.id, "over_2_5", 2.5, mv.id)

    evidence = build_market_evidence(db, prediction, match=target)

    # last5 agrupa TODO el historial disponible de ambos equipos (5 filas
    # en total, ninguno llega a 5 por si solo): totales 3,1,4,0,5 -> over
    # 2.5 en 3 de 5.
    assert evidence.windows["last5"].n == 5
    assert evidence.line_hit_frequency["last5"] == 3 / 5
    assert evidence.windows["last5"].mean == (3 + 1 + 4 + 0 + 5) / 5


def test_corners_prefers_observed_total_over_raw_match_statistics(db_session):
    db = db_session
    mv = _model_version(db)
    past = _match(db, 910201, 910202, KICKOFF - dt.timedelta(days=5), 1, 1)
    stats = MatchStatistics(match_id=past.id, home_corners=4, away_corners=4)
    db.add(stats)
    db.flush()
    pr = ProviderResponse(
        provider="test", operation="update_fixtures", path="/x", params={}, response={},
        fetched_at=dt.datetime.utcnow(),
    )
    db.add(pr)
    db.flush()
    # La observacion independiente (CornerObservation) dice 12 corners
    # reales, distinto de home_corners+away_corners=8 -- debe ganar ella.
    db.add(CornerObservation(match_id=past.id, provider_response_id=pr.id, total=12))
    db.flush()

    target = _match(db, 910201, 910202, KICKOFF, None, None, status="scheduled")
    prediction = _prediction(db, target.id, "corners_over_9_5", 9.5, mv.id)

    evidence = build_market_evidence(db, prediction, match=target)

    assert evidence.windows["last10"].n == 1
    assert evidence.windows["last10"].mean == 12.0
    assert evidence.line_hit_frequency["last10"] == 1.0


def test_home_win_excludes_rows_where_the_team_played_away(db_session):
    db = db_session
    mv = _model_version(db)
    # Equipo 1 (local del partido evaluado): una vez jugo de local y
    # perdio (no cuenta como home_win -> 0.0), una vez jugo de VISITANTE
    # y gano (debe excluirse del todo, no contar como fallo).
    _match(db, 910301, 910310, KICKOFF - dt.timedelta(days=10), 0, 1)  # local, perdio
    _match(db, 910311, 910301, KICKOFF - dt.timedelta(days=5), 0, 2)  # visitante, gano

    target = _match(db, 910301, 910302, KICKOFF, None, None, status="scheduled")
    prediction = _prediction(db, target.id, "home_win", None, mv.id)

    evidence = build_market_evidence(db, prediction, match=target)

    # Solo la fila 401 aplica (jugo de local); la 402 se excluye.
    assert evidence.windows["last10"].n == 1
    assert evidence.line_hit_frequency["last10"] == 0.0


def test_team_over_uses_the_subject_teams_own_scoring_regardless_of_venue(db_session):
    db = db_session
    mv = _model_version(db)
    # Equipo local del partido evaluado anoto 2 (local) y 0 (visitante)
    # en sus dos partidos previos -> frecuencia de over 0.5 = 1/2.
    _match(db, 910401, 910410, KICKOFF - dt.timedelta(days=10), 2, 1)
    _match(db, 910411, 910401, KICKOFF - dt.timedelta(days=5), 3, 0)

    target = _match(db, 910401, 910402, KICKOFF, None, None, status="scheduled")
    prediction = _prediction(db, target.id, "home_team_over_0_5", 0.5, mv.id)

    evidence = build_market_evidence(db, prediction, match=target)

    assert evidence.windows["last10"].n == 2
    assert evidence.line_hit_frequency["last10"] == 0.5


def test_never_leaks_a_match_that_kicks_off_after_the_evaluated_one(db_session):
    db = db_session
    mv = _model_version(db)
    _match(db, 910501, 910510, KICKOFF - dt.timedelta(days=5), 3, 2)
    # Partido FUTURO respecto al evaluado: nunca debe entrar en la evidencia.
    _match(db, 910501, 910511, KICKOFF + dt.timedelta(days=5), 5, 0)

    target = _match(db, 910501, 910502, KICKOFF, None, None, status="scheduled")
    prediction = _prediction(db, target.id, "over_2_5", 2.5, mv.id)

    evidence = build_market_evidence(db, prediction, match=target)

    assert evidence.windows["last10"].n == 1
    assert evidence.windows["last10"].mean == 5.0


def test_no_history_returns_empty_windows_and_zero_coverage_never_a_fake_value(db_session):
    db = db_session
    mv = _model_version(db)
    target = _match(db, 910601, 910602, KICKOFF, None, None, status="scheduled")
    prediction = _prediction(db, target.id, "over_2_5", 2.5, mv.id)

    evidence = build_market_evidence(db, prediction, match=target)

    assert evidence.windows["last5"].n == 0
    assert evidence.windows["last5"].mean is None
    assert evidence.line_hit_frequency["last5"] is None
    assert evidence.data_coverage == 0.0
    assert evidence.effective_sample_size == 0.0


def test_double_chance_1x_only_counts_the_home_teams_own_home_matches(db_session):
    db = db_session
    mv = _model_version(db)
    # Local del partido evaluado: de local gano una vez (cuenta: 1.0) y
    # empato otra (cuenta: 1.0); de visitante perdio (se excluye).
    _match(db, 910701, 910710, KICKOFF - dt.timedelta(days=15), 2, 0)
    _match(db, 910701, 910711, KICKOFF - dt.timedelta(days=10), 1, 1)
    _match(db, 910712, 910701, KICKOFF - dt.timedelta(days=5), 3, 0)

    target = _match(db, 910701, 910702, KICKOFF, None, None, status="scheduled")
    prediction = _prediction(db, target.id, "double_chance_1x", None, mv.id)

    evidence = build_market_evidence(db, prediction, match=target)

    assert evidence.windows["last10"].n == 2
    assert evidence.line_hit_frequency["last10"] == 1.0
