from __future__ import annotations

import datetime as dt

import pytest

from backend.app.config.settings import Settings
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.ranking import (
    ExclusionReason,
    evaluate_quality_gate,
    odds_age_minutes,
    rank_signals,
    rank_signals_v2,
    signal_score,
    signal_score_v2,
    uncertainty_factor,
)


def _prediction(**overrides) -> Prediction:
    defaults = dict(
        match_id=1,
        market="over_2_5",
        line=2.5,
        selection="over",
        model_version_id=1,
        model_probability=0.7,
        market_probability=0.55,
        market_odds=1.82,
        fair_odds=1.43,
        edge=0.15,
        expected_value=0.274,
        confidence=0.8,
        data_quality=0.8,
        bookmakers_used=4,
        bookmakers_count=4,
        explanation={"factors": []},
        features_used={"feature_names": []},
        created_at=dt.datetime.utcnow(),
    )
    defaults.update(overrides)
    return Prediction(**defaults)


def test_prediction_without_market_is_excluded():
    prediction = _prediction(market_probability=None, market_odds=None, edge=None)
    assert evaluate_quality_gate(prediction) == ExclusionReason.NO_MARKET


def test_prediction_with_invalid_odds_is_excluded():
    prediction = _prediction(market_odds=1.0)
    assert evaluate_quality_gate(prediction) == ExclusionReason.INVALID_ODDS


def test_prediction_with_odds_above_max_is_excluded():
    """Feedback real de usuario: un edge grande en un resultado muy
    improbable (modelo ~12%, cuota justa 8, mercado ofrece 15) es
    matematicamente un edge real pero no una prediccion practica para
    destacar -- sigue siendo mas probable que falle que que acierte."""
    prediction = _prediction(
        model_probability=0.125, market_probability=0.0667, market_odds=15.0, edge=0.058
    )
    assert evaluate_quality_gate(prediction) == ExclusionReason.ODDS_TOO_HIGH


def test_max_signal_odds_is_configurable_and_can_be_disabled():
    prediction = _prediction(market_odds=15.0)
    assert evaluate_quality_gate(prediction, Settings(max_signal_odds=20.0)) is None
    assert evaluate_quality_gate(prediction, Settings(max_signal_odds=None)) is None


def test_prediction_with_negative_edge_is_excluded():
    prediction = _prediction(edge=-0.05)
    assert evaluate_quality_gate(prediction) == ExclusionReason.INVALID_EDGE


def test_prediction_with_missing_edge_is_excluded():
    prediction = _prediction(edge=None)
    assert evaluate_quality_gate(prediction) == ExclusionReason.INVALID_EDGE


def test_prediction_with_too_few_bookmakers_is_excluded():
    settings = Settings(min_bookmakers=3)
    prediction = _prediction(bookmakers_used=1)
    assert evaluate_quality_gate(prediction, settings) == ExclusionReason.INSUFFICIENT_BOOKMAKERS


def test_stale_odds_disabled_by_default():
    """Por defecto (`max_odds_age_minutes=None`) no se excluye nada por
    antigueedad -- seccion 12: "no inventar frescura" pero tampoco
    descartar senhales sin evidencia de que haga falta."""
    old_prediction = _prediction(created_at=dt.datetime.utcnow() - dt.timedelta(days=30))
    assert evaluate_quality_gate(old_prediction) is None


def test_stale_odds_excluded_when_threshold_configured():
    settings = Settings(max_odds_age_minutes=60)
    fresh = _prediction(created_at=dt.datetime.utcnow() - dt.timedelta(minutes=10))
    stale = _prediction(created_at=dt.datetime.utcnow() - dt.timedelta(minutes=120))
    assert evaluate_quality_gate(fresh, settings) is None
    assert evaluate_quality_gate(stale, settings) == ExclusionReason.STALE_ODDS


def test_odds_age_minutes_reflects_created_at():
    prediction = _prediction(created_at=dt.datetime.utcnow() - dt.timedelta(minutes=45))
    age = odds_age_minutes(prediction)
    assert 44.0 <= age <= 46.0


def test_prediction_passing_all_checks_is_not_excluded():
    prediction = _prediction()
    assert evaluate_quality_gate(prediction) is None


def test_score_of_extreme_probability_tiny_edge_is_near_zero():
    """98% de probabilidad a cuota 1.02 (mercado tambien ~98%): edge casi
    nulo. No deberia puntuar alto solo por la probabilidad altisima."""
    near_certain_low_value = _prediction(
        model_probability=0.98,
        market_probability=0.975,
        market_odds=1.02,
        edge=0.005,
        confidence=0.8,
        data_quality=0.8,
    )
    solid_value = _prediction(
        model_probability=0.80,
        market_probability=0.70,
        market_odds=1.35,
        edge=0.10,
        confidence=0.8,
        data_quality=0.8,
    )
    assert signal_score(near_certain_low_value) < signal_score(solid_value)


def test_score_keeps_high_probability_and_mediocre_high_edge_in_comparable_range():
    """El ejemplo del usuario: 80% a cuota 1.30-1.40 (edge~10pp) debe
    quedar en un orden de magnitud COMPARABLE a una jugada mediocre tipo
    55% a cuota 3.0 con mas puntos de edge en bruto (edge~22pp), gracias al
    cuadrado de la probabilidad — sin el cuadrado, el edge en puntos
    porcentuales domina completamente el ranking (ver el caso sin elevar
    al cuadrado, comentado abajo), dejando la jugada de 80% muy por debajo
    pese a ser la mas solida de las dos. Con el cuadrado, quedan a menos
    de un 10% de diferencia entre si en vez de una diferencia de varias
    veces."""
    solid_value = _prediction(model_probability=0.80, edge=0.10, confidence=0.8, data_quality=0.8)
    mediocre_high_edge = _prediction(
        model_probability=0.55, edge=0.22, confidence=0.8, data_quality=0.8
    )

    ratio = signal_score(mediocre_high_edge) / signal_score(solid_value)
    assert ratio < 1.10  # comparable, no un dominio aplastante del edge en bruto

    # Sin el cuadrado (probabilidad lineal), el edge en bruto domina mucho
    # mas: 0.55*0.22=0.121 vs 0.80*0.10=0.08 -> ratio ~1.51, muy por encima
    # del ratio ~1.04 que da el cuadrado.
    linear_ratio = (0.55 * 0.22) / (0.80 * 0.10)
    assert linear_ratio > ratio


def test_rank_signals_separates_included_and_excluded_with_reasons():
    good = _prediction(match_id=1)
    no_market = _prediction(match_id=2, market_probability=None, market_odds=None, edge=None)
    bad_odds = _prediction(match_id=3, market_odds=1.0)

    included, excluded = rank_signals([good, no_market, bad_odds])

    assert len(included) == 1
    assert included[0].prediction is good
    assert {e.reason for e in excluded} == {ExclusionReason.NO_MARKET, ExclusionReason.INVALID_ODDS}


def test_rank_signals_orders_by_score_descending():
    low = _prediction(
        match_id=1, model_probability=0.85, market_probability=0.80, market_odds=1.50, edge=0.05
    )
    high = _prediction(
        match_id=2, model_probability=0.80, market_probability=0.70, market_odds=1.60, edge=0.10
    )

    included, _ = rank_signals([low, high])

    assert [s.prediction.match_id for s in included] == [2, 1]


def test_signal_score_penalizes_low_market_quality_tier():
    """Seccion 1/18 del brief: una cuota outlier/poco respaldada no debe
    poder ganar el ranking solo por probabilidad/edge -- `market_quality`
    (numero de casas Y dispersion entre ellas) es un factor propio del
    `signal_score`, no solo un componente diluido dentro de `confidence`."""
    high_quality = _prediction(
        bookmakers_used=10, market_odds_min=1.80, market_odds_max=1.85, market_odds_median=1.82
    )
    low_quality = _prediction(
        bookmakers_used=1, market_odds_min=1.82, market_odds_max=1.82, market_odds_median=1.82
    )
    assert signal_score(low_quality) < signal_score(high_quality)


def test_signal_score_market_track_record_defaults_to_neutral():
    """Sin historico (parametro no pasado), un mercado nuevo no se penaliza
    por falta de dato (seccion 1: 'no se inventa evidencia negativa')."""
    prediction = _prediction()
    assert signal_score(prediction) == signal_score(prediction, market_track_record=1.0)
    assert signal_score(prediction, market_track_record=0.5) == pytest.approx(
        signal_score(prediction) * 0.5
    )


def test_rank_signals_excludes_cuota_1_02_even_with_extreme_probability():
    """El bug real reportado por el usuario: una cuota 1.02 (modelo 98%,
    edge practicamente nulo) NO debe aparecer en 'Mejores señales' nunca,
    ni siquiera si ese dia no hay ninguna otra senhal candidata -- por eso
    se descarta en el FILTRO DURO (MIN_SIGNAL_ODDS=1.45 por defecto), no
    solo se penaliza en el scoring (que ya la hundia, pero no la excluia
    si no habia competencia)."""
    near_certain_low_value = _prediction(
        match_id=1, model_probability=0.98, market_probability=0.975, market_odds=1.02, edge=0.005
    )
    included, excluded = rank_signals([near_certain_low_value])
    assert included == []
    assert excluded[0].reason == ExclusionReason.INVALID_ODDS


class _FakeCompetition:
    def __init__(self, code: str) -> None:
        self.code = code


class _FakeMatch:
    def __init__(self, competition_code: str) -> None:
        self.competition = _FakeCompetition(competition_code)


def test_rank_signals_looks_up_market_track_record_by_competition_and_market():
    """Fase 9: rank_signals ahora puede recibir el track record REAL
    (services/evaluation_service.py::market_track_records) y lo aplica por
    (competition_code, market) de cada prediccion, no un numero global."""
    good_history = _prediction(match_id=1, market="over_2_5")
    good_history.match = _FakeMatch("laliga")
    bad_history = _prediction(match_id=2, market="over_2_5")
    bad_history.match = _FakeMatch("premier")

    track_records = {("laliga", "over_2_5"): 1.0, ("premier", "over_2_5"): 0.4}
    included, _ = rank_signals([good_history, bad_history], market_track_records=track_records)

    scores = {s.prediction.match_id: s.score for s in included}
    assert scores[1] == pytest.approx(scores[2] / 0.4)


def test_rank_signals_defaults_to_neutral_when_match_or_entry_is_missing():
    """Sin relacion `match` cargada (tipico en tests que no tocan BD) o sin
    entrada para esa (competicion, mercado) en el dict (historico
    insuficiente): nunca debe reventar, y el score debe ser el neutro de
    siempre (seccion 1: 'no inventar evidencia negativa ni positiva')."""
    no_match_relation = _prediction(match_id=1)
    assert no_match_relation.match is None

    with_match_but_unknown_history = _prediction(match_id=2)
    with_match_but_unknown_history.match = _FakeMatch("serie_a")

    included_a, _ = rank_signals(
        [no_match_relation], market_track_records={("laliga", "over_2_5"): 0.5}
    )
    included_b, _ = rank_signals(
        [with_match_but_unknown_history], market_track_records={("laliga", "over_2_5"): 0.5}
    )
    assert included_a[0].score == pytest.approx(signal_score(no_match_relation))
    assert included_b[0].score == pytest.approx(signal_score(with_match_but_unknown_history))


# ---------------------------------------------------------------------------
# signal_score_v2 (revision "motor profesional 2.0")


def test_uncertainty_factor_is_neutral_without_interval_data():
    assert uncertainty_factor(None, None) == 1.0
    assert uncertainty_factor(0.5, None) == 1.0


def test_uncertainty_factor_is_one_for_a_zero_width_interval():
    assert uncertainty_factor(0.7, 0.7) == 1.0


def test_uncertainty_factor_decreases_monotonically_with_width():
    narrow = uncertainty_factor(0.70, 0.80)  # ancho 0.10
    medium = uncertainty_factor(0.50, 0.90)  # ancho 0.40
    wide = uncertainty_factor(0.10, 0.95)  # ancho 0.85
    assert narrow > medium > wide


def test_uncertainty_factor_never_drops_below_the_floor():
    assert uncertainty_factor(0.0, 1.0) == pytest.approx(0.30)  # ancho maximo posible


def test_signal_score_v2_always_uses_raw_model_probability_never_calibrated():
    """Diseno inicial de v2 preferia `calibrated_probability` cuando
    existia -- corregido con evidencia real: `ensemble_calibration_report`
    corrido contra LaLiga para home_win/draw/away_win/double_chance_1x
    (los 4 mercados centrales del caso que motivo esta revision) muestra
    que isotonic Y Platt scaling empeoran Brier/log-loss/ECE frente a la
    cruda en los 4, de forma consistente -- preferir la calibrada seria
    preferir una probabilidad demostradamente peor. v2 ignora
    `calibrated_probability` por completo."""
    same_model_probability_different_calibration = _prediction(
        model_probability=0.60,
        calibrated_probability=0.90,  # deliberadamente muy distinta -- debe ser irrelevante
        risk_adjusted_expected_value=0.10,
        uncertainty_low=0.55,
        uncertainty_high=0.65,
    )
    same_model_probability_no_calibration = _prediction(
        model_probability=0.60,
        calibrated_probability=None,
        risk_adjusted_expected_value=0.10,
        uncertainty_low=0.55,
        uncertainty_high=0.65,
    )
    assert signal_score_v2(same_model_probability_different_calibration) == pytest.approx(
        signal_score_v2(same_model_probability_no_calibration)
    )


def test_signal_score_v2_uses_risk_adjusted_ev_not_raw_edge():
    """A diferencia de v1 (que usa `edge`), v2 ignora `edge` por completo y
    usa `risk_adjusted_expected_value` -- cambiar solo `edge` no debe mover
    el score_v2 nada."""
    base = _prediction(risk_adjusted_expected_value=0.20)
    only_edge_changed = _prediction(risk_adjusted_expected_value=0.20, edge=0.99)
    assert signal_score_v2(base) == pytest.approx(signal_score_v2(only_edge_changed))

    different_ev = _prediction(risk_adjusted_expected_value=0.40)
    assert signal_score_v2(different_ev) > signal_score_v2(base)


def test_signal_score_v2_is_zero_for_negative_risk_adjusted_ev():
    negative_ev = _prediction(risk_adjusted_expected_value=-0.10)
    assert signal_score_v2(negative_ev) == 0.0


def test_signal_score_v2_penalizes_wide_uncertainty_interval():
    narrow = _prediction(
        risk_adjusted_expected_value=0.20, uncertainty_low=0.75, uncertainty_high=0.85
    )
    wide = _prediction(
        risk_adjusted_expected_value=0.20, uncertainty_low=0.30, uncertainty_high=0.90
    )
    assert signal_score_v2(wide) < signal_score_v2(narrow)


def test_signal_score_v2_never_favors_the_getafe_style_extreme_case():
    """El caso real que motivo toda la revision: modelo 81.9% / mercado
    41.9% (edge brutal, +40pp) con la probabilidad poco respaldada
    (intervalo ancho [0.40, 0.95]) frente a una senhal mas modesta pero
    solida (edge menor, intervalo estrecho). v1 (que solo mira
    probabilidad^2 * edge) favorece claramente a la extrema; v2 NO debe
    hacerlo."""
    extreme_but_shaky = _prediction(
        match_id=1,
        model_probability=0.819,
        calibrated_probability=None,
        market_probability=0.419,
        market_odds=2.39,
        edge=0.40,
        expected_value=0.957,
        risk_adjusted_expected_value=-0.04,  # con uncertainty_low=0.40 y cuota 2.39: 0.40*2.39-1
        uncertainty_low=0.40,
        uncertainty_high=0.95,
        confidence=0.6,
        data_quality=0.6,
    )
    solid_and_well_supported = _prediction(
        match_id=2,
        model_probability=0.65,
        calibrated_probability=None,
        market_probability=0.55,
        market_odds=1.75,
        edge=0.10,
        expected_value=0.1375,
        risk_adjusted_expected_value=0.05,
        uncertainty_low=0.60,
        uncertainty_high=0.70,
        confidence=0.8,
        data_quality=0.8,
    )

    # v1 (el problema real reportado): la extrema puntua mas alto solo por
    # el edge/probabilidad en bruto.
    assert signal_score(extreme_but_shaky) > signal_score(solid_and_well_supported)

    # v2 (la correccion): la extrema, con EV ajustado al riesgo negativo,
    # queda en CERO -- la solida gana.
    assert signal_score_v2(extreme_but_shaky) == 0.0
    assert signal_score_v2(solid_and_well_supported) > signal_score_v2(extreme_but_shaky)


def test_rank_signals_v2_orders_by_score_v2_descending():
    low = _prediction(match_id=1, risk_adjusted_expected_value=0.05)
    high = _prediction(match_id=2, risk_adjusted_expected_value=0.30)

    included, _ = rank_signals_v2([low, high])

    assert [s.prediction.match_id for s in included] == [2, 1]


def test_rank_signals_v2_uses_the_same_hard_quality_gate_as_v1():
    good = _prediction(match_id=1)
    bad_odds = _prediction(match_id=2, market_odds=1.0)

    included, excluded = rank_signals_v2([good, bad_odds])

    assert len(included) == 1
    assert included[0].prediction is good
    assert excluded[0].reason == ExclusionReason.INVALID_ODDS
