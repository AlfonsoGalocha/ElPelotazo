"""Fase D de la revision v2.8: categorias A+/A/B/C/D.

`opportunity_category` es pura (Prediction + nivel de contradiccion +
fiabilidad + flags, todo ya calculado por el llamador) -- igual que
`test_contradiction.py`, sin BD."""

from __future__ import annotations

import datetime as dt

from backend.app.config.settings import Settings
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.anomaly import CONTRADICTION, HIGH_UNCERTAINTY, OUTLIER, ContradictionLevel
from backend.app.prediction.opportunity_category import (
    A_PLUS_MAX_UNCERTAINTY_WIDTH,
    A_PLUS_MIN_TRACK_RECORD_OBSERVATIONS,
    OpportunityCategory,
    opportunity_category,
)
from backend.app.services.evaluation_service import MIN_SETTLED_FOR_TRACK_RECORD, TrackRecordEstimate

NOW = dt.datetime(2025, 6, 1, 12, 0)
SETTINGS = Settings()


def _prediction(**overrides) -> Prediction:
    """Una prediccion que, por defecto, pasa TANTO el filtro general
    (v1/v2) COMO "Mejores oportunidades" (v3) -- los tests de cada
    categoria rompen un unico aspecto a la vez desde esta base."""
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


EXACT_RELIABILITY = TrackRecordEstimate(value=0.65, source_level="competition_market", n_observations=50)


def _category(prediction, contradiction_level, reliability, anomaly_flags) -> OpportunityCategory:
    return opportunity_category(prediction, contradiction_level, reliability, anomaly_flags, SETTINGS, now=NOW)


def test_d_cuando_no_pasa_el_filtro_general_de_sanidad():
    prediction = _prediction(market_odds=None, market_probability=None)

    category = _category(prediction, ContradictionLevel.SIN_CONTRADICCION, EXACT_RELIABILITY, [])

    assert category == OpportunityCategory.D


def test_d_cuando_la_contradiccion_es_fuerte_aunque_todo_lo_demas_sea_perfecto():
    prediction = _prediction()

    category = _category(prediction, ContradictionLevel.FUERTE, EXACT_RELIABILITY, [])

    assert category == OpportunityCategory.D


def test_d_cuando_hay_flag_de_contradiccion_binaria():
    prediction = _prediction()

    category = _category(prediction, ContradictionLevel.SIN_CONTRADICCION, EXACT_RELIABILITY, [CONTRADICTION])

    assert category == OpportunityCategory.D


def test_d_cuando_hay_flag_de_alta_incertidumbre():
    prediction = _prediction()

    category = _category(prediction, ContradictionLevel.SIN_CONTRADICCION, EXACT_RELIABILITY, [HIGH_UNCERTAINTY])

    assert category == OpportunityCategory.D


def test_c_cuando_pasa_el_filtro_general_pero_no_v3_por_falta_de_calibracion():
    prediction = _prediction(calibrated_probability=None)

    category = _category(prediction, ContradictionLevel.SIN_CONTRADICCION, None, [])

    assert category == OpportunityCategory.C


def test_c_cuando_pasa_v3_pero_la_contradiccion_es_moderada():
    prediction = _prediction()

    category = _category(prediction, ContradictionLevel.MODERADA, EXACT_RELIABILITY, [])

    assert category == OpportunityCategory.C


def test_c_cuando_pasa_v3_pero_la_evidencia_es_insuficiente():
    prediction = _prediction()

    category = _category(prediction, ContradictionLevel.EVIDENCIA_INSUFICIENTE, EXACT_RELIABILITY, [])

    assert category == OpportunityCategory.C


def test_b_cuando_la_fiabilidad_no_es_del_par_exacto():
    prediction = _prediction()
    shrunk_reliability = TrackRecordEstimate(value=0.6, source_level="market_tier", n_observations=200)

    category = _category(prediction, ContradictionLevel.SIN_CONTRADICCION, shrunk_reliability, [])

    assert category == OpportunityCategory.B


def test_b_cuando_la_contradiccion_es_leve_aunque_la_fiabilidad_sea_exacta():
    prediction = _prediction()

    category = _category(prediction, ContradictionLevel.LEVE, EXACT_RELIABILITY, [])

    assert category == OpportunityCategory.B


def test_b_cuando_hay_una_flag_de_anomalia_no_binaria_aunque_todo_lo_demas_sea_perfecto():
    prediction = _prediction()

    category = _category(prediction, ContradictionLevel.SIN_CONTRADICCION, EXACT_RELIABILITY, [OUTLIER])

    assert category == OpportunityCategory.B


def test_a_cuando_todo_es_perfecto_pero_la_muestra_de_fiabilidad_no_es_suficientemente_grande_para_a_plus():
    prediction = _prediction(uncertainty_low=0.69, uncertainty_high=0.71)  # ancho 0.02, estrecho
    reliability = TrackRecordEstimate(
        value=0.65, source_level="competition_market", n_observations=MIN_SETTLED_FOR_TRACK_RECORD
    )

    category = _category(prediction, ContradictionLevel.SIN_CONTRADICCION, reliability, [])

    assert category == OpportunityCategory.A


def test_a_cuando_todo_es_perfecto_pero_el_intervalo_de_incertidumbre_no_es_suficientemente_estrecho_para_a_plus():
    # Ancho 0.32: por encima de A_PLUS_MAX_UNCERTAINTY_WIDTH (0.30) pero por
    # debajo de HIGH_UNCERTAINTY_WIDTH_THRESHOLD (0.60, no activa flag);
    # uncertainty_low se mantiene en 0.68 para que el EV conservador
    # (0.68*1.5-1=0.02) siga siendo positivo y v3 no falle por otro motivo.
    prediction = _prediction(uncertainty_low=0.68, uncertainty_high=1.0)
    reliability = TrackRecordEstimate(
        value=0.65, source_level="competition_market", n_observations=A_PLUS_MIN_TRACK_RECORD_OBSERVATIONS * 2
    )

    category = _category(prediction, ContradictionLevel.SIN_CONTRADICCION, reliability, [])

    assert category == OpportunityCategory.A


def test_a_plus_cuando_todo_es_perfecto_y_la_muestra_y_la_incertidumbre_cumplen_el_umbral_estricto():
    prediction = _prediction(uncertainty_low=0.69, uncertainty_high=0.70)  # ancho 0.01 < A_PLUS_MAX_UNCERTAINTY_WIDTH
    assert 0.70 - 0.69 < A_PLUS_MAX_UNCERTAINTY_WIDTH
    reliability = TrackRecordEstimate(
        value=0.65, source_level="competition_market", n_observations=A_PLUS_MIN_TRACK_RECORD_OBSERVATIONS
    )

    category = _category(prediction, ContradictionLevel.SIN_CONTRADICCION, reliability, [])

    assert category == OpportunityCategory.A_PLUS


def test_jerarquia_d_es_mas_fuerte_que_cualquier_otra_condicion():
    """D domina incluso si el resto de senhales serian compatibles con A+."""
    prediction = _prediction(uncertainty_low=0.69, uncertainty_high=0.70)
    reliability = TrackRecordEstimate(
        value=0.65, source_level="competition_market", n_observations=A_PLUS_MIN_TRACK_RECORD_OBSERVATIONS
    )

    category = _category(prediction, ContradictionLevel.FUERTE, reliability, [])

    assert category == OpportunityCategory.D


def test_es_reproducible_la_misma_entrada_da_siempre_la_misma_categoria():
    prediction = _prediction()

    first = _category(prediction, ContradictionLevel.SIN_CONTRADICCION, EXACT_RELIABILITY, [])
    second = _category(prediction, ContradictionLevel.SIN_CONTRADICCION, EXACT_RELIABILITY, [])

    assert first == second == OpportunityCategory.A
