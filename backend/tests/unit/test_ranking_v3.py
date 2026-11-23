"""Tests de "Mejores oportunidades" (v3, cambio de filosofia del ranking,
pedido real del usuario): el filtro duro (`evaluate_quality_gate_v3`), el
orden de prioridad LEXICOGRAFICO (`ranking_key_v3`, nunca un score
combinado con pesos inventados), la deduplicacion a una senhal por
partido (`select_one_per_match`) y el ensamblado completo
(`rank_signals_v3`). Distinto de `test_ranking.py` (v1/v2): v3 tiene su
PROPIO filtro duro, deliberadamente independiente (ver comentario al
inicio de `evaluate_quality_gate_v3` en prediction/ranking.py)."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.app.config.settings import Settings
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.ranking import (
    ExclusionReason,
    calibrated_probability_or_fallback,
    conservative_probability,
    ev_with_probability,
    evaluate_quality_gate_v3,
    rank_signals_v3,
    ranking_key_v3,
    select_one_per_match,
    RankedSignal,
)


def _prediction(**overrides) -> Prediction:
    defaults = dict(
        match_id=1,
        market="over_2_5",
        line=2.5,
        selection="over",
        model_version_id=1,
        model_probability=0.72,
        market_probability=0.55,
        market_odds=1.70,
        fair_odds=1.39,
        edge=0.15,
        expected_value=0.19,
        confidence=0.8,
        data_quality=0.8,
        bookmakers_used=4,
        bookmakers_count=4,
        calibrated_probability=0.72,
        uncertainty_low=0.65,
        uncertainty_high=0.78,
        explanation={"factors": []},
        features_used={"feature_names": []},
        odds_origin_at=dt.datetime.utcnow() - dt.timedelta(minutes=1),
        created_at=dt.datetime.utcnow(),
    )
    defaults.update(overrides)
    return Prediction(**defaults)


class _FakeCompetition:
    def __init__(self, code: str) -> None:
        self.code = code


class _FakeMatch:
    def __init__(self, competition_code: str) -> None:
        self.competition = _FakeCompetition(competition_code)


# ---------------------------------------------------------------------------
# calibrated_probability_or_fallback / conservative_probability / ev_with_probability


def test_calibrated_probability_or_fallback_uses_calibrated_when_present():
    prediction = _prediction(calibrated_probability=0.63, model_probability=0.70)
    assert calibrated_probability_or_fallback(prediction) == 0.63


def test_calibrated_probability_or_fallback_falls_back_to_model_probability():
    """Mercado/competicion sin holdout suficiente (MIN_CALIBRATION_MATCHES):
    nunca se descarta la prediccion ni se inventa una calibracion."""
    prediction = _prediction(calibrated_probability=None, model_probability=0.70)
    assert calibrated_probability_or_fallback(prediction) == 0.70


def test_conservative_probability_is_the_minimum_with_uncertainty_low():
    prediction = _prediction(calibrated_probability=0.70, uncertainty_low=0.55)
    assert conservative_probability(prediction) == 0.55


def test_conservative_probability_never_exceeds_calibrated_even_if_uncertainty_low_is_higher():
    prediction = _prediction(calibrated_probability=0.70, uncertainty_low=0.90)
    assert conservative_probability(prediction) == 0.70


def test_conservative_probability_falls_back_to_calibrated_without_uncertainty_data():
    prediction = _prediction(calibrated_probability=0.70, uncertainty_low=None)
    assert conservative_probability(prediction) == 0.70


def test_ev_with_probability_is_none_without_real_market_odds():
    assert ev_with_probability(0.70, None) is None


def test_ev_with_probability_formula():
    assert ev_with_probability(0.60, 2.0) == pytest.approx(0.20)


# ---------------------------------------------------------------------------
# evaluate_quality_gate_v3: filtro duro


def test_v3_excludes_prediction_without_market():
    prediction = _prediction(market_odds=None, market_probability=None)
    assert evaluate_quality_gate_v3(prediction) == ExclusionReason.NO_MARKET


def test_v3_excludes_odds_below_the_opportunity_band():
    prediction = _prediction(market_odds=1.29)
    assert evaluate_quality_gate_v3(prediction) == ExclusionReason.ODDS_OUTSIDE_OPPORTUNITY_BAND


def test_v3_excludes_odds_above_the_opportunity_band():
    """Nada por encima de 1.80 debe aparecer nunca en "Mejores oportunidades"
    (seccion 2 del pedido), aunque EV/probabilidad/fiabilidad serian
    impecables -- es el requisito explicito del usuario."""
    prediction = _prediction(market_odds=1.81)
    assert evaluate_quality_gate_v3(prediction) == ExclusionReason.ODDS_OUTSIDE_OPPORTUNITY_BAND


@pytest.mark.parametrize("boundary_odds", [1.30, 1.80])
def test_v3_opportunity_band_boundaries_are_inclusive(boundary_odds):
    """Seccion 2: "Ambos limites son inclusivos" -- 1.30 y 1.80 exactos
    deben PASAR la comprobacion de cuota (pueden seguir excluidos por otra
    razon distinta, pero nunca por ODDS_OUTSIDE_OPPORTUNITY_BAND)."""
    prediction = _prediction(market_odds=boundary_odds)
    reason = evaluate_quality_gate_v3(prediction, market_track_record=0.6)
    assert reason != ExclusionReason.ODDS_OUTSIDE_OPPORTUNITY_BAND


def test_v3_opportunity_band_is_configurable_and_independent_from_signal_odds():
    """`min_opportunity_odds`/`max_opportunity_odds` son una banda
    DISTINTA de `min_signal_odds`/`max_signal_odds` (v1/v2) -- una cuota
    de 1.40 debe pasar v3 con la config por defecto aunque un
    `min_signal_odds` mas alto (configurado aparte) la hubiera excluido
    en v1/v2."""
    prediction = _prediction(market_odds=1.40)
    settings = Settings(min_signal_odds=1.45, min_opportunity_odds=1.35, max_opportunity_odds=1.90)
    reason = evaluate_quality_gate_v3(prediction, settings, market_track_record=0.6)
    assert reason != ExclusionReason.ODDS_OUTSIDE_OPPORTUNITY_BAND


def test_v3_excludes_insufficient_bookmakers():
    settings = Settings(min_bookmakers=3)
    prediction = _prediction(bookmakers_used=1)
    assert evaluate_quality_gate_v3(prediction, settings) == ExclusionReason.INSUFFICIENT_BOOKMAKERS


def test_v3_excludes_low_data_quality():
    settings = Settings(min_data_quality=0.5)
    prediction = _prediction(data_quality=0.2)
    assert evaluate_quality_gate_v3(prediction, settings) == ExclusionReason.LOW_DATA_QUALITY


def test_v3_excludes_stale_odds_when_configured():
    settings = Settings(max_odds_age_minutes=30)
    prediction = _prediction(odds_origin_at=dt.datetime.utcnow() - dt.timedelta(hours=2))
    assert evaluate_quality_gate_v3(prediction, settings, market_track_record=0.6) == ExclusionReason.STALE_ODDS


def test_v3_excludes_non_positive_estimated_ev():
    """calibrada 0.50 a cuota 1.70 => EV = 0.50*1.70-1 = -0.15: nunca debe
    pasar, aunque el edge nominal (model_probability-market_probability)
    fuera positivo."""
    prediction = _prediction(calibrated_probability=0.50, model_probability=0.50, market_odds=1.70)
    assert evaluate_quality_gate_v3(prediction) == ExclusionReason.NON_POSITIVE_ESTIMATED_EV


def test_v3_excludes_non_positive_conservative_ev_even_with_positive_estimated_ev():
    """calibrada 0.65 a cuota 1.70 => EV_estimado = 0.105 (positivo), pero
    uncertainty_low=0.55 => EV_conservador = 0.55*1.70-1 = -0.065
    (negativo): seccion 4 del pedido, el EV conservador debe ser positivo
    POR SEPARADO del estimado, nunca basta uno solo."""
    prediction = _prediction(calibrated_probability=0.65, uncertainty_low=0.55, market_odds=1.70)
    assert (
        evaluate_quality_gate_v3(prediction, market_track_record=0.6)
        == ExclusionReason.NON_POSITIVE_CONSERVATIVE_EV
    )


def test_v3_excludes_when_market_track_record_is_missing():
    """A diferencia de v1/v2 (neutro 1.0 sin historico), v3 EXIGE
    fiabilidad historica real para entrar (seccion 4/5 del pedido) -- sin
    ella, la prediccion queda fuera aunque EV/cuota serian impecables."""
    prediction = _prediction()
    assert (
        evaluate_quality_gate_v3(prediction, market_track_record=None)
        == ExclusionReason.INSUFFICIENT_TRACK_RECORD
    )


def test_v3_passes_when_every_hard_requirement_is_satisfied():
    prediction = _prediction(market_odds=1.70, calibrated_probability=0.72, uncertainty_low=0.65)
    assert evaluate_quality_gate_v3(prediction, market_track_record=0.6) is None


def test_v3_never_requires_a_minimum_edge():
    """Seccion 5: el edge es "informacion secundaria", deliberadamente sin
    minimo exigido -- un edge nominal de 0 (o negativo, si el mercado
    estuviera mas optimista que la calibracion) no debe excluir una
    prediccion con EV estimado/conservador positivos."""
    prediction = _prediction(edge=-0.02, calibrated_probability=0.72, uncertainty_low=0.65, market_odds=1.70)
    assert evaluate_quality_gate_v3(prediction, market_track_record=0.6) is None


# ---------------------------------------------------------------------------
# ranking_key_v3: orden de prioridad lexicografico


def test_ranking_key_v3_prioritizes_calibrated_probability_over_everything_else():
    """El caso literal del pedido (seccion 5): una prediccion con 44% de
    probabilidad y cuota 3.50 NUNCA debe superar a otra con 72% y cuota
    1.70, aunque la primera tuviera mejor EV conservador, mejor fiabilidad
    historica y mejor (menor) incertidumbre -- la probabilidad calibrada
    decide primero, por construccion de la tupla, siempre."""
    high_probability_low_odds = _prediction(
        calibrated_probability=0.72, uncertainty_low=0.70, uncertainty_high=0.74, market_odds=1.70, data_quality=0.5
    )
    low_probability_high_odds = _prediction(
        calibrated_probability=0.44, uncertainty_low=0.40, uncertainty_high=0.48, market_odds=3.50, data_quality=0.99
    )
    key_high_prob = ranking_key_v3(high_probability_low_odds, market_track_record=0.3)
    key_low_prob = ranking_key_v3(low_probability_high_odds, market_track_record=0.99)
    assert key_high_prob > key_low_prob


def test_ranking_key_v3_breaks_probability_ties_with_conservative_ev():
    same_probability_better_ev = _prediction(calibrated_probability=0.70, uncertainty_low=0.70, market_odds=1.90)
    same_probability_worse_ev = _prediction(calibrated_probability=0.70, uncertainty_low=0.70, market_odds=1.45)
    key_better = ranking_key_v3(same_probability_better_ev, market_track_record=0.5)
    key_worse = ranking_key_v3(same_probability_worse_ev, market_track_record=0.5)
    assert key_better > key_worse


def test_ranking_key_v3_breaks_probability_and_ev_ties_with_track_record():
    prediction_a = _prediction(calibrated_probability=0.70, uncertainty_low=0.70, market_odds=1.70)
    prediction_b = _prediction(calibrated_probability=0.70, uncertainty_low=0.70, market_odds=1.70)
    key_a = ranking_key_v3(prediction_a, market_track_record=0.9)
    key_b = ranking_key_v3(prediction_b, market_track_record=0.4)
    assert key_a > key_b


def test_ranking_key_v3_prefers_narrower_uncertainty_once_everything_else_ties():
    narrow = _prediction(calibrated_probability=0.70, uncertainty_low=0.68, uncertainty_high=0.72, market_odds=1.70)
    wide = _prediction(calibrated_probability=0.70, uncertainty_low=0.68, uncertainty_high=0.95, market_odds=1.70)
    key_narrow = ranking_key_v3(narrow, market_track_record=0.5)
    key_wide = ranking_key_v3(wide, market_track_record=0.5)
    assert key_narrow > key_wide


def test_ranking_key_v3_treats_missing_uncertainty_width_as_the_worst_case():
    """Ancho de incertidumbre ausente se trata como el PEOR caso (1.0 de
    ancho) para el orden, nunca como una certeza no medida."""
    missing_width = _prediction(
        calibrated_probability=0.70, uncertainty_low=0.70, uncertainty_high=None, market_odds=1.70
    )
    # Mismo `uncertainty_low` (=> mismo EV conservador) que `missing_width`,
    # solo para aislar el componente de ANCHO (indice 3 de la tupla) --
    # ancho explicito de 1.0 (0.70 a 1.70) debe dar la MISMA clave que el
    # ancho ausente (que tambien cae al peor caso, 1.0).
    known_wide_width = _prediction(
        calibrated_probability=0.70, uncertainty_low=0.70, uncertainty_high=1.70, market_odds=1.70
    )
    key_missing = ranking_key_v3(missing_width, market_track_record=0.5)
    key_known = ranking_key_v3(known_wide_width, market_track_record=0.5)
    assert key_missing == key_known


def test_ranking_key_v3_uses_edge_only_as_the_last_tiebreaker():
    higher_edge = _prediction(
        calibrated_probability=0.70, uncertainty_low=0.70, uncertainty_high=0.74, market_odds=1.70, data_quality=0.8, edge=0.30
    )
    lower_edge = _prediction(
        calibrated_probability=0.70, uncertainty_low=0.70, uncertainty_high=0.74, market_odds=1.70, data_quality=0.8, edge=0.01
    )
    key_higher = ranking_key_v3(higher_edge, market_track_record=0.5)
    key_lower = ranking_key_v3(lower_edge, market_track_record=0.5)
    assert key_higher > key_lower


def test_ranking_key_v3_is_a_plain_tuple_never_a_combined_weighted_score():
    """"No inventes pesos para construir un score" (seccion 5): se
    comprueba por construccion que la clave es literalmente una tupla de
    6 componentes, nunca un `float` combinado."""
    prediction = _prediction()
    key = ranking_key_v3(prediction, market_track_record=0.5)
    assert isinstance(key, tuple)
    assert len(key) == 6


# ---------------------------------------------------------------------------
# select_one_per_match


def test_select_one_per_match_keeps_only_the_first_signal_of_each_match():
    best_of_match_1 = RankedSignal(prediction=_prediction(match_id=1, market="over_2_5"), score=(0.9,))
    worse_of_match_1 = RankedSignal(prediction=_prediction(match_id=1, market="under_2_5"), score=(0.5,))
    only_of_match_2 = RankedSignal(prediction=_prediction(match_id=2, market="over_2_5"), score=(0.8,))

    selected = select_one_per_match([best_of_match_1, worse_of_match_1, only_of_match_2])

    assert [s.prediction.match_id for s in selected] == [1, 2]
    assert selected[0].prediction.market == "over_2_5"


def test_select_one_per_match_is_a_no_op_when_every_match_is_distinct():
    signals = [
        RankedSignal(prediction=_prediction(match_id=1), score=(0.9,)),
        RankedSignal(prediction=_prediction(match_id=2), score=(0.8,)),
        RankedSignal(prediction=_prediction(match_id=3), score=(0.7,)),
    ]
    assert select_one_per_match(signals) == signals


def test_select_one_per_match_never_mutates_or_drops_rows_outside_the_output_list():
    """Seccion 6: las candidatas descartadas de la lista de salida siguen
    todas guardadas (esto es solo una decision de que MOSTRAR) -- esta
    prueba comprueba que la funcion es pura (no toca la lista de
    entrada)."""
    signals = [
        RankedSignal(prediction=_prediction(match_id=1), score=(0.9,)),
        RankedSignal(prediction=_prediction(match_id=1), score=(0.5,)),
    ]
    original_length = len(signals)
    select_one_per_match(signals)
    assert len(signals) == original_length


# ---------------------------------------------------------------------------
# rank_signals_v3: ensamblado completo


def test_rank_signals_v3_returns_empty_included_when_nothing_qualifies():
    """Seccion 7: "nunca rellenes el ranking con predicciones que
    incumplan los filtros" -- sin historico real (track record), debe
    quedar vacio en vez de forzar algo."""
    prediction = _prediction()
    prediction.match = _FakeMatch("laliga_test")
    included, excluded = rank_signals_v3([prediction], market_track_records={})
    assert included == []
    assert excluded[0].reason == ExclusionReason.INSUFFICIENT_TRACK_RECORD


def test_rank_signals_v3_looks_up_track_record_by_competition_and_market():
    prediction = _prediction(market="over_2_5")
    prediction.match = _FakeMatch("laliga_test")
    track_records = {("laliga_test", "over_2_5"): 0.6}

    included, excluded = rank_signals_v3([prediction], market_track_records=track_records)

    assert excluded == []
    assert len(included) == 1
    assert included[0].score == ranking_key_v3(prediction, market_track_record=0.6)


def test_rank_signals_v3_orders_by_calibrated_probability_first():
    strong = _prediction(match_id=1, calibrated_probability=0.72, uncertainty_low=0.68, market_odds=1.70)
    strong.match = _FakeMatch("laliga_test")
    weak = _prediction(match_id=2, calibrated_probability=0.60, uncertainty_low=0.58, market_odds=1.80)
    weak.match = _FakeMatch("laliga_test")
    track_records = {("laliga_test", "over_2_5"): 0.6}

    included, _ = rank_signals_v3([weak, strong], market_track_records=track_records)

    assert [s.prediction.match_id for s in included] == [1, 2]


def test_rank_signals_v3_deduplicates_to_one_prediction_per_match_by_default():
    """Seccion 6: dos mercados candidatos del MISMO partido (over_2_5 a
    cuota mejor, under_2_5 a cuota peor) solo deben producir UNA
    oportunidad en el resultado por defecto."""
    over = _prediction(match_id=1, market="over_2_5", calibrated_probability=0.72, uncertainty_low=0.68, market_odds=1.70)
    over.match = _FakeMatch("laliga_test")
    under = _prediction(match_id=1, market="under_2_5", calibrated_probability=0.60, uncertainty_low=0.58, market_odds=1.75)
    under.match = _FakeMatch("laliga_test")
    track_records = {("laliga_test", "over_2_5"): 0.6, ("laliga_test", "under_2_5"): 0.6}

    included, _ = rank_signals_v3([under, over], market_track_records=track_records)

    assert len(included) == 1
    assert included[0].prediction.market == "over_2_5"


def test_rank_signals_v3_can_keep_every_candidate_when_dedup_is_disabled():
    """`one_per_match=False` -- usado por /best/debug y por el comparador
    de backtesting, que SI quiere ver todas las candidatas liquidadas."""
    over = _prediction(match_id=1, market="over_2_5", calibrated_probability=0.72, uncertainty_low=0.68, market_odds=1.70)
    over.match = _FakeMatch("laliga_test")
    under = _prediction(match_id=1, market="under_2_5", calibrated_probability=0.60, uncertainty_low=0.58, market_odds=1.75)
    under.match = _FakeMatch("laliga_test")
    track_records = {("laliga_test", "over_2_5"): 0.6, ("laliga_test", "under_2_5"): 0.6}

    included, _ = rank_signals_v3([under, over], market_track_records=track_records, one_per_match=False)

    assert {s.prediction.market for s in included} == {"over_2_5", "under_2_5"}


def test_rank_signals_v3_never_includes_odds_outside_the_configured_band():
    """Comprobacion directa pedida por el usuario: "comprueba que ninguna
    oportunidad fuera del rango de cuotas aparece"."""
    too_high = _prediction(match_id=1, market_odds=2.50, calibrated_probability=0.50, uncertainty_low=0.45)
    too_high.match = _FakeMatch("laliga_test")
    too_low = _prediction(match_id=2, market_odds=1.10, calibrated_probability=0.95, uncertainty_low=0.92)
    too_low.match = _FakeMatch("laliga_test")
    in_band = _prediction(match_id=3, market_odds=1.70, calibrated_probability=0.72, uncertainty_low=0.68)
    in_band.match = _FakeMatch("laliga_test")
    track_records = {("laliga_test", "over_2_5"): 0.6}

    included, excluded = rank_signals_v3([too_high, too_low, in_band], market_track_records=track_records)

    assert [s.prediction.match_id for s in included] == [3]
    assert all(0.0 < s.prediction.market_odds for s in included)
    excluded_reasons = {s.prediction.match_id: s.reason for s in excluded}
    assert excluded_reasons[1] == ExclusionReason.ODDS_OUTSIDE_OPPORTUNITY_BAND
    assert excluded_reasons[2] == ExclusionReason.ODDS_OUTSIDE_OPPORTUNITY_BAND


def test_fresh_prediction_does_not_make_unknown_or_future_odds_fresh():
    assert evaluate_quality_gate_v3(_prediction(odds_origin_at=None)) is ExclusionReason.UNVERIFIED_ODDS_ORIGIN
    future = _prediction(odds_origin_at=dt.datetime.utcnow() + dt.timedelta(minutes=1))
    assert evaluate_quality_gate_v3(future) is ExclusionReason.UNVERIFIED_ODDS_ORIGIN


def test_main_opportunities_require_calibration_and_interval():
    assert evaluate_quality_gate_v3(_prediction(calibrated_probability=None)) is ExclusionReason.NO_CALIBRATION
    assert evaluate_quality_gate_v3(_prediction(uncertainty_low=None)) is ExclusionReason.NO_UNCERTAINTY
