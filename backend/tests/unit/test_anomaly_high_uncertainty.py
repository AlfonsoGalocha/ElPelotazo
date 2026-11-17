"""Revision "motor profesional 2.0" (Fase 3/5/6/8): un edge bruto enorme
(modelo 80% vs mercado 40%) no debe mostrarse automaticamente como una
oportunidad excelente cuando la propia probabilidad del modelo esta poco
respaldada (intervalo de incertidumbre ancho, ver prediction/uncertainty.py)
-- caso real reportado por el usuario (Getafe vs Atletico Madrid,
double_chance_1x: modelo 81.9% / mercado 41.9%, edge +40pp).

Ver prediction/anomaly.py::HIGH_UNCERTAINTY / HIGH_UNCERTAINTY_WIDTH_THRESHOLD.
"""

from __future__ import annotations

from backend.app.prediction.anomaly import (
    HIGH_UNCERTAINTY,
    compute_anomaly_flags,
    is_strong_signal,
)


class _Stub:
    def __init__(
        self,
        id=1,
        market="home_win",
        model_probability=0.5,
        edge=0.0,
        confidence=0.8,
        data_quality=0.8,
        bookmakers_used=5,
        uncertainty_low=None,
        uncertainty_high=None,
    ):
        self.id = id
        self.market = market
        self.model_probability = model_probability
        self.edge = edge
        self.confidence = confidence
        self.data_quality = data_quality
        self.bookmakers_used = bookmakers_used
        self.market_odds_min = 1.9
        self.market_odds_max = 2.0
        self.market_odds_median = 1.95
        self.uncertainty_low = uncertainty_low
        self.uncertainty_high = uncertainty_high


def test_wide_uncertainty_interval_flags_high_uncertainty_even_with_huge_edge():
    """El caso real que motivo la revision: edge enorme (+40pp) pero la
    probabilidad detras tiene un intervalo muy ancho -- debe marcarse, por
    mucho que el edge bruto sea llamativo."""
    prediction = _Stub(
        model_probability=0.819,
        edge=0.40,
        uncertainty_low=0.20,
        uncertainty_high=0.95,  # ancho = 0.75, muy por encima del umbral (0.60)
    )
    flags = compute_anomaly_flags(prediction, [prediction])
    assert HIGH_UNCERTAINTY in flags
    assert not is_strong_signal(flags)


def test_narrow_uncertainty_interval_never_flagged_regardless_of_edge():
    """Mismo edge grande, pero con un intervalo ESTRECHO (probabilidad bien
    respaldada): no debe marcarse -- un edge grande con buen respaldo SI es
    una senhal fuerte de verdad."""
    prediction = _Stub(
        model_probability=0.80,
        edge=0.35,
        uncertainty_low=0.65,
        uncertainty_high=0.90,  # ancho = 0.25, por debajo del umbral (0.60)
    )
    flags = compute_anomaly_flags(prediction, [prediction])
    assert HIGH_UNCERTAINTY not in flags
    assert is_strong_signal(flags)


def test_width_just_above_threshold_is_flagged():
    prediction = _Stub(uncertainty_low=0.20, uncertainty_high=0.81)  # ancho = 0.61
    flags = compute_anomaly_flags(prediction, [prediction])
    assert HIGH_UNCERTAINTY in flags


def test_width_just_below_threshold_is_not_flagged():
    prediction = _Stub(uncertainty_low=0.20, uncertainty_high=0.79)  # ancho = 0.59
    flags = compute_anomaly_flags(prediction, [prediction])
    assert HIGH_UNCERTAINTY not in flags


def test_missing_uncertainty_interval_is_never_flagged():
    """`uncertainty_low`/`uncertainty_high` ausentes (`None`) nunca se
    tratan como "maxima incertidumbre": se omite la flag, igual que el
    resto del proyecto nunca inventa un numero cuando falta el dato."""
    prediction = _Stub(uncertainty_low=None, uncertainty_high=None)
    flags = compute_anomaly_flags(prediction, [prediction])
    assert HIGH_UNCERTAINTY not in flags


def test_duck_typed_object_without_uncertainty_attributes_does_not_crash():
    """Objetos stub que ni siquiera definen `uncertainty_low`/`uncertainty_high`
    (como los ya usados en test_signal_exclusions.py) deben seguir
    funcionando sin AttributeError."""

    class _BareStub:
        id = 1
        market = "home_win"
        model_probability = 0.5
        edge = 0.1
        confidence = 0.8
        data_quality = 0.8
        bookmakers_used = 5
        market_odds_min = 1.9
        market_odds_max = 2.0
        market_odds_median = 1.95

    bare = _BareStub()
    flags = compute_anomaly_flags(bare, [bare])
    assert HIGH_UNCERTAINTY not in flags
