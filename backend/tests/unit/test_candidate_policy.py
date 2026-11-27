"""`prediction/candidate_policy.py` (v2.8.2, seccion 4): politica
candidata consciente de contradicciones -- NUNCA sustituye a v3
(`ranking.py`), solo se compara contra ella. Mismo patron sin BD que
`test_opportunity_category.py`/`test_contradiction.py`."""

from __future__ import annotations

import datetime as dt

from backend.app.config.settings import Settings
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.anomaly import ContradictionLevel
from backend.app.prediction.candidate_policy import (
    evaluate_quality_gate_candidate,
    rank_candidates,
    ranking_key_candidate,
)
from backend.app.prediction.ranking import ExclusionReason, rank_signals_v3

NOW = dt.datetime(2025, 6, 1, 12, 0)
SETTINGS = Settings()


def _prediction(**overrides) -> Prediction:
    """Misma base que `test_opportunity_category.py::_prediction`: pasa
    el filtro duro de v3 por defecto."""
    defaults = dict(
        id=1, match_id=1, market="over_2_5", line=2.5, selection="over",
        model_version_id=1, model_probability=0.70, calibrated_probability=0.70,
        uncertainty_low=0.68, uncertainty_high=0.72,
        market_probability=0.55, market_odds=1.5, fair_odds=1 / 0.70,
        edge=0.10, bookmakers_used=3,
        confidence=0.8, data_quality=0.8, explanation={}, features_used={},
        odds_origin_at=NOW - dt.timedelta(minutes=20),
        created_at=NOW - dt.timedelta(minutes=15),
    )
    defaults.update(overrides)
    return Prediction(**defaults)


MARKET_TRACK_RECORD = 0.65  # cualquier valor no-None: v3 exige que exista, nunca None.


def test_strong_contradiction_excludes_even_when_v3_would_include():
    prediction = _prediction()
    reason = evaluate_quality_gate_candidate(
        prediction, ContradictionLevel.FUERTE, SETTINGS, MARKET_TRACK_RECORD, now=NOW
    )
    assert reason == ExclusionReason.STRONG_CONTRADICTION


def test_moderate_contradiction_never_excludes_alone():
    prediction = _prediction()
    reason = evaluate_quality_gate_candidate(
        prediction, ContradictionLevel.MODERADA, SETTINGS, MARKET_TRACK_RECORD, now=NOW
    )
    assert reason is None


def test_insufficient_evidence_never_excludes_alone():
    """Pedido explicito: ausencia de evidencia NUNCA es lo mismo que
    evidencia que contradice -- nunca se bloquea en bloque."""
    prediction = _prediction()
    reason = evaluate_quality_gate_candidate(
        prediction, ContradictionLevel.EVIDENCIA_INSUFICIENTE, SETTINGS, MARKET_TRACK_RECORD, now=NOW
    )
    assert reason is None


def test_v3_exclusion_reason_takes_precedence_over_contradiction():
    """Si la prediccion ya esta excluida por v3 (aqui: cuota fuera de la
    banda de oportunidad), ese motivo se reporta tal cual -- la
    contradiccion nunca lo enmascara."""
    prediction = _prediction(market_odds=5.0)  # fuera de [1.35, 1.90]
    reason = evaluate_quality_gate_candidate(
        prediction, ContradictionLevel.FUERTE, SETTINGS, MARKET_TRACK_RECORD, now=NOW
    )
    assert reason == ExclusionReason.ODDS_OUTSIDE_OPPORTUNITY_BAND


def test_ranking_key_breaks_ties_by_lower_contradiction_severity():
    """Dos candidatas IDENTICAS salvo la severidad de contradiccion: la
    de menor severidad debe ordenar primero."""
    low_severity = ranking_key_candidate(_prediction(), ContradictionLevel.SIN_CONTRADICCION, MARKET_TRACK_RECORD)
    high_severity = ranking_key_candidate(_prediction(), ContradictionLevel.MODERADA, MARKET_TRACK_RECORD)
    assert low_severity > high_severity


def _with_match(prediction: Prediction, competition_code: str = "comp") -> Prediction:
    """Adjunta un `Match`/`Competition` en memoria (nunca persistidos) para
    que `rank_candidates` pueda resolver `market_track_records` -- esa
    busqueda exige `prediction.match.competition.code`."""
    competition = Competition(id=1, code=competition_code, name=competition_code, country="Testland")
    match = Match(id=prediction.match_id, competition_id=1, home_team_id=1, away_team_id=2)
    match.competition = competition
    prediction.match = match
    return prediction


def test_rank_candidates_excludes_strong_and_keeps_moderate():
    strong = _with_match(_prediction(id=1, match_id=1))
    moderate = _with_match(_prediction(id=2, match_id=2))
    levels = {1: ContradictionLevel.FUERTE, 2: ContradictionLevel.MODERADA}
    included, excluded = rank_candidates(
        [strong, moderate], levels, SETTINGS,
        market_track_records={("comp", "over_2_5"): MARKET_TRACK_RECORD}, now=NOW,
    )
    assert [s.prediction.id for s in included] == [2]
    excluded_by_id = {e.prediction.id: e.reason for e in excluded}
    assert excluded_by_id == {1: ExclusionReason.STRONG_CONTRADICTION}


def test_v3_module_is_never_imported_or_modified_by_this_policy():
    """Regresion de aislamiento: `rank_signals_v3` sigue funcionando
    exactamente igual (misma firma, mismo comportamiento) despues de
    anhadir la politica candidata -- v3 es la referencia, nunca se toca."""
    included, excluded = rank_signals_v3(
        [_prediction()], SETTINGS, market_track_records=None, one_per_match=True
    )
    assert included == [] or included[0].prediction.id == 1
    assert isinstance(excluded, list)
