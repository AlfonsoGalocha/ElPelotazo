from __future__ import annotations

import datetime as dt

import pytest

from backend.app.config.settings import Settings
from backend.app.db.models.modeling import Prediction
from backend.app.prediction.anomaly import best_prediction_per_match
from backend.app.prediction.ranking import (
    ExclusionReason,
    evaluate_quality_gate,
    fit_longshot_calibration_curve_from_observations,
    odds_age_minutes,
    rank_signals,
    rank_signals_v2,
    reality_capped_expected_value,
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


def test_max_signal_odds_default_excludes_the_real_getafe_style_longshot():
    """Regresion directa del caso real que motivo bajar `max_signal_odds`
    de 6.0 a 4.0 (seccion 16 de docs/architecture_audit.md): Getafe-Atletico
    "Victoria local" a cuota 5.50 (modelo 33.5%, edge 16.5pp) seguia
    apareciendo como "Destacado"/"Mejor oportunidad" en produccion pese a
    todos los techos de EV ya activos (secciones 12-15) -- la evidencia
    real por bucket de cuota (`debug_odds_vs_hitrate.py`) muestra que el
    ROI historico se desploma justo por encima de cuota 4, asi que el
    filtro DURO (nunca solo el scoring) es el que tiene que excluirlo."""
    getafe_style_longshot = _prediction(
        model_probability=0.335, market_probability=0.17, market_odds=5.50, edge=0.165
    )
    assert evaluate_quality_gate(getafe_style_longshot) == ExclusionReason.ODDS_TOO_HIGH

    # Justo por debajo del nuevo umbral: sigue pasando el filtro duro (el
    # scoring, no este filtro, sigue siendo quien pondera cuotas 3-4,
    # donde la evidencia real muestra un ROI mas cercano al vig normal).
    below_threshold = _prediction(market_odds=3.90)
    assert evaluate_quality_gate(below_threshold) is None


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
    fresh = _prediction(odds_origin_at=dt.datetime.utcnow() - dt.timedelta(minutes=10))
    stale = _prediction(odds_origin_at=dt.datetime.utcnow() - dt.timedelta(minutes=120))
    assert evaluate_quality_gate(fresh, settings) is None
    assert evaluate_quality_gate(stale, settings) == ExclusionReason.STALE_ODDS


def test_odds_age_minutes_reflects_provider_update():
    prediction = _prediction(odds_origin_at=dt.datetime.utcnow() - dt.timedelta(minutes=45))
    age = odds_age_minutes(prediction)
    assert 44.0 <= age <= 46.0


def test_odds_age_minutes_now_override_measures_against_a_different_instant():
    """Bug real corregido (auditoria v2.8): `backtesting/ranking_comparison.py`
    evalua predicciones YA LIQUIDADAS (semanas/meses en el pasado) --
    sin un `now` explicito, `odds_age_minutes` siempre comparaba contra
    el reloj REAL, asi que cualquier liquidacion historica salia como
    "antiquisima" sin importar su fiabilidad real (ver v3 nunca
    aparecia en el walk-forward). Con `now` explicito, se mide contra el
    instante que de verdad importa -- el momento evaluado, nunca hoy."""
    historical_kickoff = dt.datetime(2024, 1, 15, 12, 0)
    prediction = _prediction(
        created_at=historical_kickoff - dt.timedelta(minutes=15),
        odds_origin_at=historical_kickoff - dt.timedelta(minutes=20),
    )
    # Contra el reloj REAL, esta prediccion es antiquisima (anhos).
    assert odds_age_minutes(prediction) > 60 * 24 * 365
    # Contra el instante correcto (poco despues de su propio kickoff),
    # sigue siendo "fresca" -- exactamente lo que era en su momento.
    age_at_the_time = odds_age_minutes(prediction, now=historical_kickoff)
    assert 19.0 <= age_at_the_time <= 21.0


def test_evaluate_quality_gate_now_override_never_marks_old_settled_predictions_stale():
    settings = Settings(max_odds_age_minutes=60)
    historical_kickoff = dt.datetime(2024, 1, 15, 12, 0)
    prediction = _prediction(
        created_at=historical_kickoff - dt.timedelta(minutes=15),
        odds_origin_at=historical_kickoff - dt.timedelta(minutes=20),
    )
    # Sin `now`, el reloj real hace que parezca desactualizada.
    assert evaluate_quality_gate(prediction, settings) == ExclusionReason.STALE_ODDS
    # Con el `now` correcto (el instante real evaluado), pasa el filtro.
    assert evaluate_quality_gate(prediction, settings, now=historical_kickoff) is None


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


# reality_capped_expected_value (fix real tras activar v2: el caso
# Getafe-Atletico -- match_id=61, edge +39.6pp -- seguia arriba del
# ranking. `scripts/debug_edge_vs_hitrate.py`, corrido por el usuario
# sobre 1133 predicciones liquidadas reales, confirmo que el hit rate
# cae de forma practicamente monotona con el tamanho del edge. Un primer
# intento (factor multiplicativo sobre el score final) reducia el score
# un 72% pero seguia sin ser suficiente -- verificado EN VIVO contra la
# BD real del usuario tras desplegarlo: `risk_adjusted_expected_value`
# (+0.70) nunca se corregia, asi que el descuento no alcanzaba. La
# correccion real pone un TECHO al EV mismo con la misma curva.)


def _decreasing_curve():
    """`IsotonicRegression` real (no un stub), ajustada sobre datos
    sinteticos que reproducen la FORMA de la evidencia real: hit rate alto
    con edge pequenho, cayendo con edge grande -- nunca al reves."""
    from sklearn.isotonic import IsotonicRegression

    edge_pp = [0.0, 2.0, 5.0, 8.0, 12.0, 20.0, 35.0]
    outcome = [0.55, 0.52, 0.45, 0.40, 0.35, 0.25, 0.15]
    curve = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=False, out_of_bounds="clip")
    curve.fit(edge_pp, outcome)
    return curve


def test_reality_capped_expected_value_is_neutral_without_any_curve():
    assert reality_capped_expected_value(0.70, 0.396, 2.37, None, None) == 0.70


def test_reality_capped_expected_value_is_neutral_without_edge_or_odds():
    curve = _decreasing_curve()
    assert reality_capped_expected_value(0.70, None, 2.37, curve) == 0.70
    assert reality_capped_expected_value(0.70, 0.396, None, curve) == 0.70


def test_reality_capped_expected_value_caps_an_optimistic_ev_for_a_getafe_style_edge():
    """Con el caso real (edge +39.6pp, cuota 2.37): la curva de edge
    predice un hit rate real de ~15% para ese tamanho de edge (clip al
    extremo mas alto observado, 35pp) -- eso implica un EV real de
    0.15*2.37-1 ~= -0.64, MUY lejos del +0.70 optimista que daba
    `uncertainty_low` por si solo."""
    curve = _decreasing_curve()
    capped = reality_capped_expected_value(0.70, 0.396, 2.37, curve)
    assert capped == pytest.approx(0.15 * 2.37 - 1.0, abs=1e-6)
    assert capped < 0.70


def test_reality_capped_expected_value_never_raises_ev_above_the_raw_estimate():
    """Es un TECHO, nunca un piso: si el EV ya calculado es peor que lo
    que implica la curva, se queda como esta -- nunca se "mejora"
    artificialmente."""
    curve = _decreasing_curve()
    assert reality_capped_expected_value(-0.90, 0.396, 2.37, curve) == -0.90


def test_reality_capped_expected_value_barely_touches_a_modest_well_supported_edge():
    """Un edge pequenho, donde la curva real SI respalda una probabilidad
    de acierto razonable, no se anula -- la correccion es un techo sobre
    el optimismo EXCESIVO, nunca una penalizacion generica a cualquier
    edge."""
    curve = _decreasing_curve()
    raw_ev = 0.04
    capped = reality_capped_expected_value(raw_ev, 0.02, 2.0, curve)
    real_ev = 0.52 * 2.0 - 1.0  # curva real a edge=2pp: hit rate 0.52
    assert capped == pytest.approx(min(raw_ev, real_ev), abs=1e-6)
    assert capped > -0.10  # lejos del colapso del caso Getafe


# Segunda cota real: favorite-longshot bias (seccion 13 de
# docs/architecture_audit.md). Tras corregir el edge grande, el usuario
# reporto "victoria local" a cuota 5.50/6.00 (model_probability
# 33.5%/28.3%) seguia destacada: "una cuota 5 o una cuota 6 no es una
# buena prediccion". `scripts/debug_odds_vs_hitrate.py` confirmo que el
# modelo sobrestima su propia probabilidad en esa cola.
#
# IMPORTANTE (bug real corregido antes de desplegar, visto EN VIVO: el
# caso Getafe -- model_probability=0.335, cuota 5.50 -- seguia casi sin
# penalizar): la curva se ajusta y se consulta por `market_odds`, NUNCA
# por `model_probability` -- la evidencia de arriba esta medida por
# CUOTA; un `model_probability` identico aparece tanto en apuestas bien
# calibradas (cuota baja) como mal calibradas (cuota alta), asi que
# agrupar/consultar por probabilidad diluiria la correccion.


def _decreasing_odds_curve():
    """`IsotonicRegression` real (no un stub), ajustada sobre los BUCKETS
    reales que devolvio `scripts/debug_odds_vs_hitrate.py` cuando el
    usuario lo corrio contra sus 1133 predicciones liquidadas (seccion 13
    de docs/architecture_audit.md): cuota 1-2 (hit 68.2%), 2-3 (38.7%),
    3-4 (28.4%, bien calibrado), 4-6 (13.9%, ROI -34.3%), 6+ (3.6%, ROI
    -77.9%) -- el modelo SOBRESTIMA su propia probabilidad en la cola de
    cuota alta ("favorite-longshot bias"). Puntos de cuota = punto medio
    de cada bucket real."""
    from sklearn.isotonic import IsotonicRegression

    market_odds = [1.5, 2.5, 3.5, 5.0, 8.0]
    outcome = [0.682, 0.387, 0.284, 0.139, 0.036]
    curve = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=False, out_of_bounds="clip")
    curve.fit(market_odds, outcome)
    return curve


def test_reality_capped_expected_value_is_neutral_without_odds_curve():
    assert reality_capped_expected_value(0.30, None, 5.5, None, None) == 0.30


def test_reality_capped_expected_value_caps_an_optimistic_ev_for_a_longshot_odds():
    """Favorite-longshot real: cuota 5.5 (caso Getafe: model_probability
    0.335, pero eso ya no importa aqui -- la curva de cuota no lo
    consulta). La curva predice un hit rate real muy por debajo de lo
    que necesitaria esa cuota para dar EV positivo -- el EV real
    resultante es muy negativo frente a un EV optimista calculado con
    `uncertainty_low`."""
    curve = _decreasing_odds_curve()
    capped = reality_capped_expected_value(
        0.30, None, 5.5, None, odds_reliability_curve=curve
    )
    real_hit_rate = float(curve.predict([5.5])[0])
    assert capped == pytest.approx(real_hit_rate * 5.5 - 1.0, abs=1e-6)
    assert capped < 0.0
    assert capped < 0.30


def test_reality_capped_expected_value_barely_touches_a_well_calibrated_odds_level():
    """En la zona donde el modelo SI esta bien calibrado (cuota 3.5,
    hit rate real identico al implicado por la cuota), el techo no debe
    colapsar un EV ya modesto."""
    curve = _decreasing_odds_curve()
    raw_ev = 0.02
    capped = reality_capped_expected_value(
        raw_ev, None, 3.5, None, odds_reliability_curve=curve
    )
    assert capped == pytest.approx(min(raw_ev, 0.284 * 3.5 - 1.0), abs=1e-6)
    assert capped > -0.10


def test_reality_capped_expected_value_takes_the_minimum_across_both_curves():
    """Si una senhal es mala tanto por tamanho de edge COMO por cuota
    alta, el techo final es el MINIMO de ambas cotas independientes --
    nunca un promedio, nunca solo una de las dos."""
    edge_curve = _decreasing_curve()
    odds_curve = _decreasing_odds_curve()
    capped = reality_capped_expected_value(0.70, 0.396, 5.5, edge_curve, odds_curve)
    real_ev_edge = 0.15 * 5.5 - 1.0
    real_ev_odds = float(odds_curve.predict([5.5])[0]) * 5.5 - 1.0
    assert capped == pytest.approx(min(real_ev_edge, real_ev_odds), abs=1e-6)


def test_signal_score_v2_is_neutral_without_edge_reliability_curve():
    """Sin curva (p.ej. menos de MIN_SETTLED_FOR_EDGE_RELIABILITY
    liquidaciones todavia): comportamiento IDENTICO al de antes de este
    fix -- nunca penaliza por falta de dato."""
    prediction = _prediction(risk_adjusted_expected_value=0.20, edge=0.396)
    assert signal_score_v2(prediction, edge_reliability_curve=None) == pytest.approx(
        signal_score_v2(prediction)
    )


def test_signal_score_v2_zeroes_out_a_getafe_style_extreme_edge_once_capped():
    """La correccion real: aunque `risk_adjusted_expected_value` siga
    siendo optimista (+0.70, el caso real: `uncertainty_low=0.718` sobre
    cuota 2.37), la curva real dice que un edge de este tamanho acierta
    ~15% de las veces -- eso implica un EV real muy negativo que anula
    el score entero (igual que v2 ya anulaba un EV negativo calculado
    directamente)."""
    curve = _decreasing_curve()
    extreme_edge = _prediction(
        match_id=1,
        model_probability=0.819,
        risk_adjusted_expected_value=0.70,
        edge=0.396,
        market_odds=2.37,
        uncertainty_low=0.718,
        uncertainty_high=0.911,
    )
    without_curve = signal_score_v2(extreme_edge)
    assert without_curve > 0.0  # el bug real: un EV optimista seguia dando score positivo

    with_curve = signal_score_v2(extreme_edge, edge_reliability_curve=curve)
    assert with_curve == 0.0  # el techo real deja el EV negativo -> score anulado


def test_signal_score_v2_keeps_a_modest_well_supported_edge_positive():
    """Un edge modesto, bien respaldado por la curva real, NO se anula --
    el techo solo actua sobre el optimismo excesivo de edges grandes,
    nunca penaliza cualquier edge por igual."""
    curve = _decreasing_curve()
    modest_edge = _prediction(
        match_id=2,
        model_probability=0.60,
        risk_adjusted_expected_value=0.05,
        edge=0.02,
        market_odds=2.0,
    )
    assert signal_score_v2(modest_edge, edge_reliability_curve=curve) > 0.0


def test_rank_signals_v2_passes_edge_reliability_curve_through_to_every_prediction():
    curve = _decreasing_curve()
    extreme_edge = _prediction(
        match_id=1, risk_adjusted_expected_value=0.70, edge=0.396, market_odds=2.37
    )
    modest_edge = _prediction(
        match_id=2, risk_adjusted_expected_value=0.05, edge=0.02, market_odds=2.0
    )

    included, _ = rank_signals_v2([extreme_edge, modest_edge], edge_reliability_curve=curve)

    assert [s.prediction.match_id for s in included] == [2, 1]


def test_best_prediction_per_match_passes_edge_reliability_curve_through():
    """`best_prediction_per_match` (prediction/anomaly.py) delega en
    `rank_signals_v2` -- la curva pasada debe decidir cual de las dos
    predicciones del MISMO partido es la mejor."""
    curve = _decreasing_curve()
    extreme_edge = _prediction(
        match_id=1, risk_adjusted_expected_value=0.70, edge=0.396, market_odds=2.37
    )
    modest_edge = _prediction(
        match_id=1, risk_adjusted_expected_value=0.05, edge=0.02, market_odds=2.0
    )

    without_curve = best_prediction_per_match({1: [extreme_edge, modest_edge]})[1]
    with_curve = best_prediction_per_match(
        {1: [extreme_edge, modest_edge]}, edge_reliability_curve=curve
    )[1]

    assert without_curve is extreme_edge  # sin curva: su EV optimista (+0.70) gana
    assert with_curve is modest_edge  # con la curva real: el EV de Getafe se anula


# odds_reliability_curve (favorite-longshot bias, seccion 13):
# mismo mecanismo que edge_reliability_curve, pero sobre market_odds en
# vez de edge -- "una cuota 5 o una cuota 6 no es una buena prediccion"
# aunque el edge nominal parezca razonable.


def test_signal_score_v2_is_neutral_without_odds_reliability_curve():
    prediction = _prediction(risk_adjusted_expected_value=0.20, model_probability=0.18)
    assert signal_score_v2(prediction, odds_reliability_curve=None) == pytest.approx(
        signal_score_v2(prediction)
    )


def test_signal_score_v2_zeroes_out_a_favorite_longshot_once_capped():
    """Caso real reportado tras el fix de edge: "victoria local" a cuota
    alta con model_probability bajo (33.5%/28.3% en el caso real) seguia
    destacada. Reproducido aqui con un caso mas extremo (probabilidad
    0.18, cuota 5.5) para que el efecto del techo de cuota sea
    inequivoco en la prueba: un EV optimista (`risk_adjusted_expected_value`
    calculado con `uncertainty_low`) debe anularse por completo una vez
    se conecta la curva real de cuota."""
    curve = _decreasing_odds_curve()
    longshot = _prediction(
        match_id=1,
        model_probability=0.18,
        risk_adjusted_expected_value=0.30,
        edge=0.05,
        market_odds=5.5,
    )
    without_curve = signal_score_v2(longshot)
    assert without_curve > 0.0  # el bug real: un EV optimista seguia dando score positivo

    with_curve = signal_score_v2(longshot, odds_reliability_curve=curve)
    assert with_curve == 0.0  # el techo real deja el EV negativo -> score anulado


def test_signal_score_v2_keeps_a_well_calibrated_favorite_positive():
    """En la zona de favorito (cuota 1.7, probabilidad 66%, donde la
    curva real respalda un hit rate incluso MEJOR que lo que dice el
    modelo: 68.2%), el techo de cuota no debe anular un EV ya
    modesto -- solo actua sobre el sesgo real de la cola poco probable,
    nunca sobre cualquier senhal por igual."""
    curve = _decreasing_odds_curve()
    favorite = _prediction(
        match_id=2,
        model_probability=0.66,
        risk_adjusted_expected_value=0.05,
        edge=0.05,
        market_odds=1.7,
    )
    assert signal_score_v2(favorite, odds_reliability_curve=curve) > 0.0


def test_rank_signals_v2_passes_odds_reliability_curve_through_to_every_prediction():
    """`max_signal_odds` se baja a 4.0 (seccion 16): se pasa explicito
    aqui mas alto para que el filtro DURO no se interponga -- lo que esta
    prueba quiere comprobar es el SCORING (la curva pasada por el
    llamador), no el filtro de calidad."""
    curve = _decreasing_odds_curve()
    permissive_settings = Settings(max_signal_odds=10.0)
    longshot = _prediction(
        match_id=1,
        model_probability=0.18,
        risk_adjusted_expected_value=0.30,
        edge=0.05,
        market_odds=5.5,
    )
    favorite = _prediction(
        match_id=2,
        model_probability=0.66,
        risk_adjusted_expected_value=0.05,
        edge=0.05,
        market_odds=1.7,
    )

    included, _ = rank_signals_v2(
        [longshot, favorite], settings=permissive_settings, odds_reliability_curve=curve
    )

    assert [s.prediction.match_id for s in included] == [2, 1]


def test_best_prediction_per_match_passes_odds_reliability_curve_through():
    curve = _decreasing_odds_curve()
    # EV deliberadamente muy optimista (1.0): sin esto, model_probability^2
    # ya favorece al favorito incluso sin curva (0.66^2 >> 0.18^2), lo que
    # no aislaria el efecto del techo que esta prueba quiere comprobar.
    # `max_signal_odds` se baja a 4.0 (seccion 16): se pasa explicito mas
    # alto para que el filtro DURO no excluya al longshot (cuota 5.5)
    # antes de llegar al scoring que esta prueba quiere comprobar.
    permissive_settings = Settings(max_signal_odds=10.0)
    longshot = _prediction(
        match_id=1,
        model_probability=0.18,
        risk_adjusted_expected_value=1.0,
        edge=0.05,
        market_odds=5.5,
    )
    favorite = _prediction(
        match_id=1,
        model_probability=0.66,
        risk_adjusted_expected_value=0.05,
        edge=0.05,
        market_odds=1.7,
    )

    without_curve = best_prediction_per_match(
        {1: [longshot, favorite]}, settings=permissive_settings
    )[1]
    with_curve = best_prediction_per_match(
        {1: [longshot, favorite]}, settings=permissive_settings, odds_reliability_curve=curve
    )[1]

    assert without_curve is longshot  # sin curva: su EV optimista (+0.30) gana
    assert with_curve is favorite  # con la curva real: el EV del longshot se anula


# longshot_calibration_curve (seccion 15 de docs/architecture_audit.md):
# a diferencia de odds_reliability_curve (seccion 13, DESCONECTADA de
# produccion -- colapsaba el ranking entero porque usaba ROI PROMEDIO,
# negativo en TODAS las cuotas por el vig), esta recalibra la
# PROBABILIDAD del modelo (acotada en [0,1], sin vig mezclado), y SOLO
# en la cola de cuota alta (`LONGSHOT_ODDS_THRESHOLD`) donde la seccion
# 13.2 encontro sobreconfianza real -- pedido explicito del usuario:
# "quiero que el modelo sea realista tambien con las predicciones".
# NUNCA activada en produccion todavia, pendiente de validacion
# walk-forward real (`backtesting/ranking_comparison.py`).


def _increasing_longshot_calibration_curve():
    """`IsotonicRegression` real (no un stub), ajustada sobre un patron
    que reproduce la FORMA de la evidencia real de la seccion 13.2
    (restringida a la cola, cuota>=4): el modelo dice mas de lo que
    realmente acierta, pero la relacion es creciente (mas probabilidad
    dicha, mas acierto real) -- nunca al reves."""
    from sklearn.isotonic import IsotonicRegression

    model_probability = [0.15, 0.22, 0.28, 0.35]
    outcome = [0.03, 0.08, 0.15, 0.20]
    curve = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    curve.fit(model_probability, outcome)
    return curve


def test_fit_longshot_calibration_curve_from_observations_is_increasing():
    import numpy as np

    model_probability = np.array([0.15, 0.22, 0.28, 0.35])
    outcome = np.array([0.0, 0.0, 1.0, 1.0])
    curve = fit_longshot_calibration_curve_from_observations(model_probability, outcome)
    assert curve.predict([0.15])[0] <= curve.predict([0.35])[0]


def test_reality_capped_expected_value_is_neutral_without_longshot_curve():
    assert (
        reality_capped_expected_value(
            0.70, None, 5.5, None, model_probability=0.335, longshot_calibration_curve=None
        )
        == 0.70
    )


def test_reality_capped_expected_value_is_neutral_below_longshot_odds_threshold():
    """La cola solo se corrige a partir de `LONGSHOT_ODDS_THRESHOLD`
    (cuota >=4) -- por debajo, la seccion 13.2 muestra calibracion
    razonable, y tocarla ahi repetiria el resultado negativo de la
    seccion 11.5 (calibrar el rango COMPLETO empeora Brier/ECE)."""
    curve = _increasing_longshot_calibration_curve()
    capped = reality_capped_expected_value(
        0.70, None, 3.5, None, model_probability=0.335, longshot_calibration_curve=curve
    )
    assert capped == 0.70


def test_reality_capped_expected_value_caps_an_overconfident_longshot():
    curve = _increasing_longshot_calibration_curve()
    capped = reality_capped_expected_value(
        0.70, None, 5.5, None, model_probability=0.335, longshot_calibration_curve=curve
    )
    realistic_probability = float(curve.predict([0.335])[0])
    assert capped == pytest.approx(realistic_probability * 5.5 - 1.0, abs=1e-6)
    assert capped < 0.70


def test_reality_capped_expected_value_never_raises_a_longshot_ev_above_the_raw_estimate():
    curve = _increasing_longshot_calibration_curve()
    capped = reality_capped_expected_value(
        -0.90, None, 5.5, None, model_probability=0.335, longshot_calibration_curve=curve
    )
    assert capped == -0.90


def test_reality_capped_expected_value_longshot_cap_combines_with_edge_cap():
    """Si una senhal es mala tanto por tamanho de edge COMO por
    sobreconfianza en la cola, el techo final es el MINIMO de ambas
    cotas independientes -- nunca un promedio, nunca solo una de las
    dos (mismo principio que la combinacion edge+cuota de la seccion
    13)."""
    edge_curve = _decreasing_curve()
    longshot_curve = _increasing_longshot_calibration_curve()
    capped = reality_capped_expected_value(
        0.70,
        0.05,
        5.5,
        edge_curve,
        model_probability=0.335,
        longshot_calibration_curve=longshot_curve,
    )
    real_ev_edge = float(edge_curve.predict([5.0])[0]) * 5.5 - 1.0
    real_ev_longshot = float(longshot_curve.predict([0.335])[0]) * 5.5 - 1.0
    assert capped == pytest.approx(min(real_ev_edge, real_ev_longshot), abs=1e-6)


def test_signal_score_v2_is_neutral_without_longshot_calibration_curve():
    prediction = _prediction(risk_adjusted_expected_value=0.20, model_probability=0.335)
    assert signal_score_v2(prediction, longshot_calibration_curve=None) == pytest.approx(
        signal_score_v2(prediction)
    )


def test_signal_score_v2_caps_an_overconfident_longshot_once_curve_connected():
    """Reproduce el caso real (match_id=61, home_win, model_probability
    33.5%, cuota 5.5) que seguia siendo #1 tras el rollback del techo de
    cuota (seccion 14): una vez conectada la curva de calibracion de la
    cola, su score debe bajar (nunca a cero por fuerza -- es una
    recalibracion de probabilidad, no un corte duro)."""
    curve = _increasing_longshot_calibration_curve()
    longshot = _prediction(
        match_id=1,
        model_probability=0.335,
        risk_adjusted_expected_value=0.70,
        edge=0.165,
        market_odds=5.5,
    )
    without_curve = signal_score_v2(longshot)
    with_curve = signal_score_v2(longshot, longshot_calibration_curve=curve)
    assert with_curve < without_curve


def test_rank_signals_v2_passes_longshot_calibration_curve_through_to_every_prediction():
    """`max_signal_odds` se baja a 4.0 (seccion 16): se pasa explicito
    aqui mas alto para que el filtro DURO no excluya al longshot (cuota
    5.5) antes de llegar al scoring que esta prueba quiere comprobar."""
    curve = _increasing_longshot_calibration_curve()
    permissive_settings = Settings(max_signal_odds=10.0)
    longshot = _prediction(
        match_id=1,
        model_probability=0.335,
        risk_adjusted_expected_value=0.70,
        edge=0.165,
        market_odds=5.5,
    )
    well_calibrated = _prediction(
        match_id=2,
        model_probability=0.55,
        risk_adjusted_expected_value=0.05,
        edge=0.05,
        market_odds=1.9,
    )

    included, _ = rank_signals_v2(
        [longshot, well_calibrated], settings=permissive_settings, longshot_calibration_curve=curve
    )

    assert [s.prediction.match_id for s in included] == [2, 1]
