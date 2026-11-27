"""v2.8.2 (seccion 5): `_category_cache_for_predictions`/
`_serialize_with_category_cache` (api/routes/predictions.py) -- reusan
`selector_decision_candidates` como cache real para mostrar categoria en
listas, sin recalcular `build_market_evidence`/`classify_contradiction`
al vuelo. Tres estados: completada (cache fresca), desactualizada
(cuota/calibracion cambiaron desde la captura), pendiente (nunca
capturada)."""

from __future__ import annotations

import datetime as dt
import itertools

from backend.app.api.routes.predictions import _category_cache_for_predictions, _serialize_with_category_cache
from backend.app.db.models.core import Competition
from backend.app.db.models.matches import Match
from backend.app.db.models.modeling import ModelVersion, Prediction, SelectorDecisionCandidate, SelectorDecisionSnapshot

_seq = itertools.count(1)


def _prediction(db, **overrides) -> Prediction:
    comp = Competition(code=f"cat-cache-{next(_seq)}", name="Cat Cache Test", country="Testland")
    db.add(comp)
    db.flush()
    mv = ModelVersion(
        name="cat-cache-test", version="v1", market_family="goals",
        features=[], hyperparameters={}, metrics={}, dataset_version="test",
    )
    db.add(mv)
    db.flush()
    match = Match(
        provider="cat-cache-test", provider_id=f"cat-cache-{next(_seq)}",
        competition_id=comp.id, season_id=1, kickoff_utc=dt.datetime.utcnow() + dt.timedelta(hours=5),
        home_team_id=1, away_team_id=2, status="scheduled",
    )
    db.add(match)
    db.flush()
    defaults = dict(
        match_id=match.id, market="over_2_5", line=2.5, selection="over",
        model_version_id=mv.id, model_probability=0.7, calibrated_probability=0.7,
        market_odds=1.5, fair_odds=1 / 0.7, confidence=0.8, data_quality=0.8,
        explanation={}, features_used={}, created_at=dt.datetime.utcnow(),
    )
    defaults.update(overrides)
    prediction = Prediction(**defaults)
    db.add(prediction)
    db.flush()
    return prediction


def _candidate_snapshot(db, prediction: Prediction, *, market_odds: float, calibrated_probability: float, category="A") -> None:
    snapshot = SelectorDecisionSnapshot(
        match_id=prediction.match_id, captured_at=dt.datetime.utcnow(), selector_version="v3_probability_first",
        min_opportunity_odds=1.35, max_opportunity_odds=1.90, selected_prediction_id=prediction.id,
    )
    db.add(snapshot)
    db.flush()
    db.add(SelectorDecisionCandidate(
        snapshot_id=snapshot.id, prediction_id=prediction.id, market=prediction.market,
        model_probability=prediction.model_probability, calibrated_probability=calibrated_probability,
        market_odds=market_odds, market_probability=0.55, ev_conservative=0.02,
        market_track_record_value=0.6, market_track_record_source_level="competition_market",
        uncertainty_width=0.04, data_quality=0.8, edge=0.10, category=category,
        contradiction_level="SIN_CONTRADICCION", rank_position=1, exclusion_reason=None, is_selected=True,
    ))
    db.flush()


def test_cache_hit_returns_category_without_recomputing_evidence(db_session, monkeypatch):
    db = db_session
    prediction = _prediction(db)
    _candidate_snapshot(db, prediction, market_odds=prediction.market_odds, calibrated_probability=prediction.calibrated_probability)

    def _boom(*args, **kwargs):
        raise AssertionError("no deberia llamarse build_market_evidence para una lista con cache")

    monkeypatch.setattr("backend.app.api.routes.predictions.build_market_evidence", _boom)

    cache = _category_cache_for_predictions(db, [prediction])
    entry = cache[prediction.id]
    assert entry["category"] == "A"
    assert entry["category_status"] == "completada"
    assert entry["contradiction_level"] == "SIN_CONTRADICCION"


def test_cache_miss_is_pending_never_a_favorable_category(db_session):
    db = db_session
    prediction = _prediction(db)  # nunca capturada

    cache = _category_cache_for_predictions(db, [prediction])
    entry = cache[prediction.id]
    assert entry["category"] is None
    assert entry["category_status"] == "pendiente"


def test_cache_stale_when_odds_changed_since_capture(db_session):
    db = db_session
    prediction = _prediction(db, market_odds=1.5)
    _candidate_snapshot(db, prediction, market_odds=1.5, calibrated_probability=prediction.calibrated_probability)

    # La cuota se movio DESPUES de capturarse -- nunca se debe mostrar la
    # categoria capturada como si siguiera vigente.
    prediction.market_odds = 1.75
    db.flush()

    cache = _category_cache_for_predictions(db, [prediction])
    entry = cache[prediction.id]
    assert entry["category"] == "A"  # el valor sigue disponible para contexto...
    assert entry["category_status"] == "desactualizada"  # ...pero marcado como no vigente


def test_cache_stale_when_calibration_changed_since_capture(db_session):
    db = db_session
    prediction = _prediction(db, calibrated_probability=0.70)
    _candidate_snapshot(db, prediction, market_odds=prediction.market_odds, calibrated_probability=0.70)

    prediction.calibrated_probability = 0.55  # un nuevo modelo/calibrador la cambio
    db.flush()

    cache = _category_cache_for_predictions(db, [prediction])
    assert cache[prediction.id]["category_status"] == "desactualizada"


def test_serialize_with_category_cache_uses_the_cached_entry():
    prediction = Prediction(
        id=999, match_id=1, market="over_2_5", line=2.5, selection="over", model_version_id=1,
        model_probability=0.7, calibrated_probability=0.7, confidence=0.8, data_quality=0.8,
        explanation={}, features_used={},
    )
    cache = {999: {"category": "A+", "category_status": "completada", "contradiction_level": "SIN_CONTRADICCION"}}
    serialized = _serialize_with_category_cache(prediction, cache)
    assert serialized["category"] == "A+"
    assert serialized["category_status"] == "completada"
    assert serialized["contradiction_level"] == "SIN_CONTRADICCION"
