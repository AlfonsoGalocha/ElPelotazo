"""Fase E de la revision v2.8: aprendizaje del selector sobre sus propias
decisiones (congelar snapshots + evaluacion post-hoc).

Usa la fixture `db_session` (transaccion aislada, revertida al terminar
cada test -- ver conftest.py): tanto `capture_selector_decisions` como
`evaluate_selector_decisions` escanean TODO lo que exista en la tabla sin
ningun filtro por test/competicion (es su contrato: un informe global),
asi que cada test necesita su propia transaccion aislada para no mezclar
snapshots entre tests -- `session_scope()` (que hace commit real sobre el
mismo SQLite compartido de toda la sesion de pytest) NO sirve aqui.

`now` siempre relativo al reloj real (`dt.datetime.utcnow()`): a
diferencia del comparador walk-forward (Fase A), `capture_selector_
decisions` esta pensada para usarse SIEMPRE en el presente real (no es
una herramienta de backtesting), y `rank_signals_v3`/`evaluate_quality_
gate_v3` miden la frescura de la cuota contra el reloj real por defecto
-- usar una fecha fija lejana en el pasado disparar{i}a `STALE_ODDS` por
el mismo motivo ya documentado en `backend/app/prediction/ranking.py::
odds_age_minutes`."""

from __future__ import annotations

import datetime as dt
import itertools

from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import (
    ModelVersion,
    Prediction,
    PredictionResult,
    SelectorDecisionCandidate,
    SelectorDecisionSnapshot,
)
from backend.app.services.selector_decision_service import (
    MIN_RECAPTURE_INTERVAL_MINUTES,
    capture_selector_decisions,
    evaluate_selector_decisions,
)

_seq = itertools.count(1)


def _competition(db) -> Competition:
    code = f"sd-test-{next(_seq)}"
    comp = Competition(code=code, name=code, country="Testland")
    db.add(comp)
    db.flush()
    return comp


def _model_version(db) -> ModelVersion:
    mv = ModelVersion(
        name="selector-decision-test", version="v1", market_family="goals",
        features=[], hyperparameters={}, metrics={}, dataset_version="test",
    )
    db.add(mv)
    db.flush()
    return mv


def _match(db, competition_id: int, kickoff: dt.datetime, status: str = "scheduled", **overrides) -> Match:
    defaults = dict(
        provider="selector-decision-test", provider_id=f"sd-{next(_seq)}",
        competition_id=competition_id, season_id=1, kickoff_utc=kickoff,
        home_team_id=920001, away_team_id=920002, status=status,
    )
    defaults.update(overrides)
    match = Match(**defaults)
    db.add(match)
    db.flush()
    return match


def _v3_eligible_prediction(db, match_id: int, model_version_id: int, now: dt.datetime, **overrides) -> Prediction:
    """Pasa el filtro general (v1/v2) Y "Mejores oportunidades" (v3)
    SALVO por fiabilidad historica (que depende de lo que exista en la
    BD): misma base que usan los tests de ranking_comparison/Fase D."""
    defaults = dict(
        match_id=match_id, market="over_2_5", line=2.5, selection="over",
        model_version_id=model_version_id, model_probability=0.70, calibrated_probability=0.70,
        uncertainty_low=0.68, uncertainty_high=0.72,
        market_probability=0.55, market_odds=1.5, fair_odds=1 / 0.70,
        edge=0.10, bookmakers_used=3,
        confidence=0.8, data_quality=0.8, explanation={}, features_used={},
        odds_origin_at=now - dt.timedelta(minutes=20),
        created_at=now - dt.timedelta(minutes=15),
    )
    defaults.update(overrides)
    prediction = Prediction(**defaults)
    db.add(prediction)
    db.flush()
    return prediction


def test_captura_crea_un_snapshot_con_una_candidata_por_prediccion(db_session):
    """NOTA sobre que NO se da por supuesto aqui: en la suite COMPLETA,
    `market_track_records_with_provenance` (nivel "mercado global", ver
    evaluation_service.py) puede encontrar respaldo real para "over_2_5"
    acumulado por OTROS ficheros de test (es exactamente el fallback
    jerarquico que se pide que exista) -- esta prediccion sintetica
    podria entonces SI pasar v3, dependiendo de cuanta historia real haya
    en el resto de la BD compartida en ese momento. Por eso este test NO
    asume cual de los dos casos ocurre: solo que el snapshot/candidata se
    registran de forma INTERNAMENTE CONSISTENTE en cualquiera de los dos."""
    db = db_session
    now = dt.datetime.utcnow()
    comp = _competition(db)
    mv = _model_version(db)
    match = _match(db, comp.id, now + dt.timedelta(hours=2))
    prediction = _v3_eligible_prediction(db, match.id, mv.id, now)

    created = capture_selector_decisions(db, now=now)

    # >= 1, no == 1: ver docstring de este modulo -- la funcion escanea
    # TODOS los partidos "scheduled" de la ventana, sin filtrar por
    # competicion/test.
    assert created >= 1
    snapshot = db.query(SelectorDecisionSnapshot).filter_by(match_id=match.id, captured_at=now).one()
    assert snapshot.selector_version == "v3_probability_first"

    candidates = db.query(SelectorDecisionCandidate).filter_by(snapshot_id=snapshot.id).all()
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.prediction_id == prediction.id
    if candidate.rank_position is None:
        assert candidate.exclusion_reason is not None
        assert candidate.is_selected is False
        assert snapshot.selected_prediction_id is None
    else:
        assert candidate.rank_position == 1
        assert candidate.exclusion_reason is None
        assert candidate.is_selected is True
        assert snapshot.selected_prediction_id == prediction.id


def test_captura_es_idempotente_dentro_del_intervalo_de_recaptura(db_session):
    db = db_session
    now = dt.datetime.utcnow()
    comp = _competition(db)
    mv = _model_version(db)
    match = _match(db, comp.id, now + dt.timedelta(hours=2))
    _v3_eligible_prediction(db, match.id, mv.id, now)

    capture_selector_decisions(db, now=now)
    soon_after = now + dt.timedelta(minutes=MIN_RECAPTURE_INTERVAL_MINUTES - 1)
    capture_selector_decisions(db, now=soon_after)

    # Contado SOLO para este partido (ver comentario del test anterior
    # sobre por que no se usa el valor global devuelto): debe seguir
    # habiendo exactamente un snapshot, la segunda llamada no duplica.
    assert db.query(SelectorDecisionSnapshot).filter_by(match_id=match.id).count() == 1


def test_captura_crea_un_nuevo_corte_pasado_el_intervalo_de_recaptura(db_session):
    db = db_session
    now = dt.datetime.utcnow()
    comp = _competition(db)
    mv = _model_version(db)
    match = _match(db, comp.id, now + dt.timedelta(hours=5))
    _v3_eligible_prediction(db, match.id, mv.id, now)

    capture_selector_decisions(db, now=now)
    later = now + dt.timedelta(minutes=MIN_RECAPTURE_INTERVAL_MINUTES + 1)
    capture_selector_decisions(db, now=later)

    snapshots = (
        db.query(SelectorDecisionSnapshot)
        .filter_by(match_id=match.id)
        .order_by(SelectorDecisionSnapshot.captured_at)
        .all()
    )
    assert len(snapshots) == 2
    assert snapshots[0].captured_at == now
    assert snapshots[1].captured_at == later


def test_un_snapshot_existente_nunca_se_modifica_por_una_recaptura_dentro_del_intervalo(db_session):
    db = db_session
    now = dt.datetime.utcnow()
    comp = _competition(db)
    mv = _model_version(db)
    match = _match(db, comp.id, now + dt.timedelta(hours=2))
    _v3_eligible_prediction(db, match.id, mv.id, now)

    capture_selector_decisions(db, now=now)
    snapshot = db.query(SelectorDecisionSnapshot).filter_by(match_id=match.id).one()
    candidate = db.query(SelectorDecisionCandidate).filter_by(snapshot_id=snapshot.id).one()
    original_captured_at, original_category = snapshot.captured_at, candidate.category

    capture_selector_decisions(db, now=now + dt.timedelta(minutes=1))

    db.refresh(snapshot)
    db.refresh(candidate)
    assert snapshot.captured_at == original_captured_at
    assert candidate.category == original_category


def test_nunca_toca_prediction_ni_prediction_result_existentes(db_session):
    db = db_session
    now = dt.datetime.utcnow()
    comp = _competition(db)
    mv = _model_version(db)
    match = _match(db, comp.id, now + dt.timedelta(hours=2))
    prediction = _v3_eligible_prediction(db, match.id, mv.id, now)
    original_probability, original_odds = prediction.model_probability, prediction.market_odds

    capture_selector_decisions(db, now=now)

    db.refresh(prediction)
    assert prediction.model_probability == original_probability
    assert prediction.market_odds == original_odds


def _settled_snapshot(db, selected_won: bool, other_won: bool) -> SelectorDecisionSnapshot:
    """Snapshot construido directamente (sin pasar por `capture_selector_
    decisions`) para controlar exactamente los resultados liquidados --
    `evaluate_selector_decisions` es la pieza bajo prueba aqui, no la
    captura."""
    comp = _competition(db)
    mv = _model_version(db)
    kickoff = dt.datetime.utcnow() - dt.timedelta(days=1)
    match = _match(db, comp.id, kickoff, status="finished", home_goals=2, away_goals=1)

    selected_pred = _v3_eligible_prediction(db, match.id, mv.id, kickoff, market="over_2_5")
    other_pred = _v3_eligible_prediction(db, match.id, mv.id, kickoff, market="btts", selection="yes")

    db.add(PredictionResult(prediction_id=selected_pred.id, outcome=selected_won, settled_at=kickoff + dt.timedelta(hours=2)))
    db.add(PredictionResult(prediction_id=other_pred.id, outcome=other_won, settled_at=kickoff + dt.timedelta(hours=2)))
    db.flush()

    snapshot = SelectorDecisionSnapshot(
        match_id=match.id, captured_at=kickoff - dt.timedelta(hours=2), selector_version="v3_probability_first",
        min_opportunity_odds=1.35, max_opportunity_odds=1.90, selected_prediction_id=selected_pred.id,
    )
    db.add(snapshot)
    db.flush()
    db.add(SelectorDecisionCandidate(
        snapshot_id=snapshot.id, prediction_id=selected_pred.id, market="over_2_5",
        calibrated_probability=0.70, ev_conservative=0.02, market_track_record_value=0.6,
        market_track_record_source_level="competition_market", uncertainty_width=0.04,
        data_quality=0.8, edge=0.10, category="A", rank_position=1, exclusion_reason=None, is_selected=True,
    ))
    db.add(SelectorDecisionCandidate(
        snapshot_id=snapshot.id, prediction_id=other_pred.id, market="btts",
        calibrated_probability=0.60, ev_conservative=0.01, market_track_record_value=0.5,
        market_track_record_source_level="market_tier", uncertainty_width=0.10,
        data_quality=0.7, edge=0.05, category="B", rank_position=2, exclusion_reason=None, is_selected=False,
    ))
    db.flush()
    return snapshot


def test_evaluate_reporta_pendiente_cuando_el_partido_aun_no_liquida(db_session):
    db = db_session
    now = dt.datetime.utcnow()
    comp = _competition(db)
    mv = _model_version(db)
    match = _match(db, comp.id, now + dt.timedelta(hours=2))
    _v3_eligible_prediction(db, match.id, mv.id, now)
    capture_selector_decisions(db, now=now)

    assert db.query(SelectorDecisionSnapshot).filter_by(match_id=match.id).count() == 1
    report = evaluate_selector_decisions(db)

    # Igual que en los tests de captura: el informe es deliberadamente
    # GLOBAL (sin filtrar por partido/competicion, ver su propio
    # contrato), asi que en la suite completa puede haber mas snapshots
    # pendientes de OTROS partidos "scheduled" reales de otros ficheros de
    # test -- lo que importa es que NINGUNO contribuya como liquidado
    # (todos son partidos "scheduled", sin PredictionResult posible).
    assert report.n_snapshots_total >= 1
    assert report.n_snapshots_pending >= 1
    assert report.selected_n == 0
    assert report.selected_win_rate is None


def test_evaluate_cuando_la_seleccionada_gana_no_hay_oportunidad_perdida(db_session):
    db = db_session
    _settled_snapshot(db, selected_won=True, other_won=False)

    report = evaluate_selector_decisions(db)

    assert report.n_snapshots_settled == 1
    assert report.selected_n == 1
    assert report.selected_win_rate == 1.0
    assert report.missed_opportunities_n == 0
    assert report.missed_opportunities_rate == 0.0


def test_evaluate_detecta_una_oportunidad_perdida_real(db_session):
    """El caso que esta fase quiere medir: la seleccionada pierde pero
    otra candidata (peor posicion) SI ganaba."""
    db = db_session
    _settled_snapshot(db, selected_won=False, other_won=True)

    report = evaluate_selector_decisions(db)

    assert report.selected_win_rate == 0.0
    assert report.missed_opportunities_n == 1
    assert report.missed_opportunities_rate == 1.0

    by_rank = {bucket.key: bucket for bucket in report.by_rank_position}
    assert by_rank["1"].win_rate == 0.0
    assert by_rank["2"].win_rate == 1.0

    by_category = {bucket.key: bucket for bucket in report.by_category}
    assert by_category["A"].win_rate == 0.0
    assert by_category["B"].win_rate == 1.0


def test_evaluate_nunca_mezcla_snapshots_distintos(db_session):
    db = db_session
    _settled_snapshot(db, selected_won=True, other_won=False)
    _settled_snapshot(db, selected_won=False, other_won=True)

    report = evaluate_selector_decisions(db)

    assert report.n_snapshots_total == 2
    assert report.n_snapshots_settled == 2
    assert report.selected_n == 2
    assert report.selected_win_rate == 0.5
    assert report.missed_opportunities_n == 1
    assert report.missed_opportunities_rate == 0.5
